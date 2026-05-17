"""SIGINT-deferral mechanism tests (Phase 5 §11.5; Phase 6 extends).

POSIX-only: ``signal.pthread_sigmask`` is not available on Windows
(ADR 0004). Windows ships best-effort flag-based handling; that path
is exercised in integration but not in this micro-unit suite.

Phase 6 adds the
``test_run_wizard_does_not_enter_commit_when_subagent_killed_by_sigint``
test: when the wizard subagent's ``Backend.chat`` raises
``KeyboardInterrupt``, the orchestrator propagates it without entering
``commit()`` — no topic dir, no about-me.md.
"""

from __future__ import annotations

import io
import os
import signal
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.console import Console

from remory import paths
from remory.backends.base import ChatResult, HeadlessMeta, HeadlessResult, HealthReport
from remory.ui import CheckResult, CheckStatus
from remory.wizard import _orchestrator as orch_mod
from remory.wizard._commit import _deferred_sigint
from remory.wizard._orchestrator import run_wizard

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only mechanism; Windows uses a flag-based handler (ADR 0004)",
)


def _install_default_sigint_handler() -> None:
    """Reinstall the default SIGINT handler.

    pytest installs a custom one for capture; we want the real Python
    KeyboardInterrupt path so the assertions below match runtime
    behaviour.
    """
    signal.signal(signal.SIGINT, signal.default_int_handler)


def test_deferred_sigint_masks_signal_during_block_then_reraises_on_exit() -> None:
    """A SIGINT delivered while masked is queued; unmask raises KeyboardInterrupt."""
    _install_default_sigint_handler()
    pid = os.getpid()

    raised = False

    def fire_signal_after_a_beat() -> None:
        # Give the main thread time to enter the masked block.
        time.sleep(0.05)
        os.kill(pid, signal.SIGINT)

    t = threading.Thread(target=fire_signal_after_a_beat, daemon=True)
    t.start()

    try:
        with _deferred_sigint():
            # Sleep long enough that the SIGINT is delivered while masked.
            # The default handler is installed but masked, so the
            # signal is queued by the kernel.
            time.sleep(0.2)
        # Should not reach this line — unmask raises KeyboardInterrupt.
    except KeyboardInterrupt:
        raised = True
    t.join(timeout=1.0)
    assert raised, "expected KeyboardInterrupt to be raised on unmask"


def test_deferred_sigint_unmask_propagates_queued_signal_as_keyboard_interrupt_to_caller() -> None:
    """Restating the contract: the queued signal lands as KeyboardInterrupt
    in the caller's frame, not as a deferred attribute or return value.
    """
    _install_default_sigint_handler()
    pid = os.getpid()

    def fire() -> None:
        time.sleep(0.05)
        os.kill(pid, signal.SIGINT)

    t = threading.Thread(target=fire, daemon=True)
    t.start()

    with pytest.raises(KeyboardInterrupt), _deferred_sigint():
        time.sleep(0.2)
    t.join(timeout=1.0)


def test_deferred_sigint_double_sigint_within_block_delivered_once_at_unmask() -> None:
    """Two SIGINTs while masked → kernel queues once; one KeyboardInterrupt on unmask.

    POSIX SIGINT is non-realtime; multiple deliveries while masked
    coalesce into a single pending signal. We assert the caller sees
    exactly one KeyboardInterrupt.
    """
    _install_default_sigint_handler()
    pid = os.getpid()

    def fire_twice() -> None:
        time.sleep(0.05)
        os.kill(pid, signal.SIGINT)
        os.kill(pid, signal.SIGINT)

    t = threading.Thread(target=fire_twice, daemon=True)
    t.start()

    interrupt_count = 0
    try:
        with _deferred_sigint():
            time.sleep(0.2)
    except KeyboardInterrupt:
        interrupt_count += 1
    # After leaving the block, no further interrupts should hit us.
    time.sleep(0.05)
    t.join(timeout=1.0)
    assert interrupt_count == 1


# ---------------------------------------------------------------------------
# Phase 6 — wizard subagent SIGINT propagation
# ---------------------------------------------------------------------------


class _AuthOKBackend:
    """Backend stub whose preflight passes; ``chat`` raises KeyboardInterrupt."""

    def __init__(self) -> None:
        self.chat_calls: list[dict[str, object]] = []

    def chat(
        self,
        *,
        cwd: Path,
        resume: bool = False,
        agent: str | None = None,
        initial_prompt: str | None = None,
    ) -> ChatResult:
        self.chat_calls.append(
            {
                "cwd": cwd,
                "resume": resume,
                "agent": agent,
                "initial_prompt": initial_prompt,
            }
        )
        raise KeyboardInterrupt

    def headless(
        self,
        *,
        prompt: str,
        agent: str | None = None,
        cwd: Path | None = None,
        json_output: bool = False,
        timeout_seconds: int = 600,
    ) -> HeadlessResult:
        del prompt, agent, cwd, json_output, timeout_seconds
        return HeadlessResult(
            text="ok",
            session_id="sigint-test",
            duration_ms=1,
            num_turns=1,
            stop_reason="end_turn",
            meta=HeadlessMeta(raw_envelope=None),
        )

    def health_check(self) -> HealthReport:
        return HealthReport(
            binary_present=True,
            binary_path=Path("/usr/bin/claude"),
            version="fake",
            authenticated=True,
            notes=(),
        )


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def test_run_wizard_does_not_enter_commit_when_subagent_killed_by_sigint(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KeyboardInterrupt from ``backend.chat`` propagates → no COMMIT."""
    data_dir = isolated_xdg / "data"
    # Force preflight OK so we reach the chat call.
    monkeypatch.setattr(
        orch_mod,
        "_check_claude_binary",
        lambda: CheckResult(
            id="claude_binary",
            status=CheckStatus.OK,
            label="claude binary",
            detail="(test)",
        ),
    )
    monkeypatch.setattr(
        orch_mod,
        "_check_claude_auth",
        lambda *, binary_present, backend_factory: CheckResult(
            id="claude_auth",
            status=CheckStatus.OK,
            label="claude auth",
            detail="(test)",
        ),
    )

    backend = _AuthOKBackend()

    with pytest.raises(KeyboardInterrupt):
        run_wizard(
            backend_factory=lambda: backend,
            console=Console(file=io.StringIO(), color_system=None, no_color=True),
            data_dir=data_dir,
        )

    # COMMIT never ran: no topic dirs, no about-me.md.
    topics_root = data_dir / "topics"
    if topics_root.exists():
        assert not any(topics_root.iterdir())
    assert not paths.about_me_file(data_dir).exists()
