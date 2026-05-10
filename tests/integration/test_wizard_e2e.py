"""End-to-end wizard tests (Phase 5, consolidated plan §11.8).

Covers the user-visible wizard sequence: scripted stdin drives the
interview, the FakeBackend returns or fails the letter LLM call, and
the assertions pin the on-disk artefacts + exit-code behaviour.
"""

from __future__ import annotations

import io
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.console import Console

from remory import paths
from remory.backends.base import BackendTimeoutError
from remory.cli.errors import TopicExistsError, format_error
from remory.locking import is_locked
from remory.wizard import (
    WizardCommitPartialError,
    WizardSigintDuringCommitError,
)
from remory.wizard import _commit as _commit_mod
from remory.wizard._orchestrator import run_wizard
from tests.fakes.fake_backend import FakeBackend
from tests.fakes.scripted_input import ScriptedInput

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only locking under test",
)


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def _quiet_console() -> Console:
    return Console(file=io.StringIO(), color_system=None, no_color=True)


def test_remory_init_runs_wizard_when_invoked_without_topic_or_schema_flag(
    isolated_xdg: Path,
) -> None:
    """Happy path: scripted stdin walks the wizard end-to-end."""
    data_dir = isolated_xdg / "data"
    # Sam, "1,2" (coaching, job-profile lex), per-topic 1,1,1,1, wish.
    fake = ScriptedInput(["Sam", "1,2", "1", "1", "1", "1", "stop forgetting"])
    backend = FakeBackend.with_letter_text("welcome paragraph")

    run_wizard(
        backend_factory=lambda: backend,
        console=_quiet_console(),
        input_fn=fake,
        data_dir=data_dir,
    )

    # Topic dirs created; lock files released.
    for name in ("coaching", "job-profile"):
        topic_dir = data_dir / "topics" / name
        assert topic_dir.is_dir()
        assert is_locked(topic_dir) is False

    # about-me.md exists with correct bytes prefix and facts.
    about_me = paths.about_me_file(data_dir).read_text(encoding="utf-8")
    assert about_me.startswith("welcome paragraph")
    assert "name: Sam\n" in about_me
    assert "topics: coaching, job-profile\n" in about_me
    assert "wish: stop forgetting\n" in about_me


def test_remory_init_wizard_renders_fallback_letter_when_fake_backend_raises_timeout(
    isolated_xdg: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Backend timeout → fallback paragraph + WARNING log with safe extras."""
    data_dir = isolated_xdg / "data"
    fake = ScriptedInput(["Sam", "2", "1", "1", "stop forgetting"])
    backend = FakeBackend.with_letter_failure(BackendTimeoutError)

    with caplog.at_level(logging.WARNING, logger="remory.wizard.letter"):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            input_fn=fake,
            data_dir=data_dir,
        )

    about_me = paths.about_me_file(data_dir).read_text(encoding="utf-8")
    # Fallback paragraph prefix.
    assert about_me.startswith("(I couldn't reach the model just now,")
    # WARNING log shape per D4: exception_type + wizard_step extras only.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].__dict__.get("exception_type") == "BackendTimeoutError"
    assert warnings[0].__dict__.get("wizard_step") == "letter"
    assert not hasattr(warnings[0], "stderr_tail")


def test_remory_init_wizard_keyboard_interrupt_pre_commit_writes_no_files_and_exits_130(
    isolated_xdg: Path,
) -> None:
    """Pre-COMMIT Ctrl+C → KeyboardInterrupt; CLI maps to exit 130."""
    data_dir = isolated_xdg / "data"
    fake = ScriptedInput(["Sam"], raise_at=1)
    backend = FakeBackend.with_letter_text("never reached")

    with pytest.raises(KeyboardInterrupt):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            input_fn=fake,
            data_dir=data_dir,
        )

    # No artefacts on disk.
    topics_root = data_dir / "topics"
    if topics_root.exists():
        assert not any(topics_root.iterdir())
    assert not paths.about_me_file(data_dir).exists()

    # CLI mapping: KeyboardInterrupt → exit 130.
    msg, code = format_error(KeyboardInterrupt(), data_dir=data_dir)
    assert code == 130
    assert msg == ""


def test_remory_init_wizard_keyboard_interrupt_during_first_topic_write_completes_in_flight_then_exits_130(  # noqa: E501  # pinned name from consolidated plan §11.8 — encodes the contract
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mid-COMMIT KeyboardInterrupt → WizardSigintDuringCommitError; exit 130."""
    data_dir = isolated_xdg / "data"
    fake = ScriptedInput(["Sam", "2", "1", "1", "stop forgetting"])
    backend = FakeBackend.with_letter_text("p")

    # Patch write_state to "complete the in-flight write" then deliver
    # a KeyboardInterrupt as if from the unmask. We simulate it
    # directly: write state, then raise KI synchronously to mirror
    # the SIGINT-on-unmask shape.
    real_write_state = _commit_mod.write_state

    def write_then_interrupt(state_path: Path, doc: object) -> None:
        real_write_state(state_path, doc)  # type: ignore[arg-type]
        raise KeyboardInterrupt

    monkeypatch.setattr(_commit_mod, "write_state", write_then_interrupt)

    with pytest.raises(WizardSigintDuringCommitError):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            input_fn=fake,
            data_dir=data_dir,
        )

    # In-flight files exist (state.md was completed); about-me.md
    # is NOT written (subsequent step skipped).
    topic_dir = data_dir / "topics" / "job-profile"
    assert topic_dir.is_dir()
    assert (topic_dir / "state.md").exists()
    assert not paths.about_me_file(data_dir).exists()

    # CLI mapping → exit 130 with locked message.
    msg, code = format_error(WizardSigintDuringCommitError("ki"), data_dir=data_dir)
    assert code == 130
    assert "Stopped mid-write." in msg


def test_remory_init_wizard_partial_failure_at_second_topic_leaves_first_topic_intact_and_exits_1(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = isolated_xdg / "data"
    # 1,2 = coaching, job-profile (lex). Per-topic 1,1,1,1, wish.
    fake = ScriptedInput(
        ["Sam", "1,2", "1", "1", "1", "1", "stop forgetting"],
    )
    backend = FakeBackend.with_letter_text("p")

    # Fail second topic's state write.
    real_write_state = _commit_mod.write_state
    call_count = {"n": 0}

    def fail_on_second(state_path: Path, doc: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated disk-full")
        real_write_state(state_path, doc)  # type: ignore[arg-type]

    monkeypatch.setattr(_commit_mod, "write_state", fail_on_second)

    with pytest.raises(WizardCommitPartialError) as ei:
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            input_fn=fake,
            data_dir=data_dir,
        )
    assert ei.value.failed_topic == "job-profile"
    assert ei.value.prior_topic == "coaching"

    # First topic intact.
    assert (data_dir / "topics" / "coaching" / "state.md").exists()
    # CLI mapping → exit 1 with two-topic wording.
    msg, code = format_error(ei.value, data_dir=data_dir)
    assert code == 1
    assert "Topic 'coaching' was created" in msg


def test_remory_init_wizard_refuses_when_chosen_topic_already_exists_with_topic_exists_message(
    isolated_xdg: Path,
) -> None:
    """User picks an already-existing topic; refusal fires inside COMMIT."""
    data_dir = isolated_xdg / "data"
    # Pre-create the workout topic dir to trigger refusal.
    pre = data_dir / "topics" / "workout"
    pre.mkdir(parents=True)

    # Pick "3" = workout (the conflicting one).
    fake = ScriptedInput(["Sam", "3", "1", "1", "stop forgetting"])
    backend = FakeBackend.with_letter_text("p")

    with pytest.raises(TopicExistsError) as ei:
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            input_fn=fake,
            data_dir=data_dir,
        )
    assert ei.value.name == "workout"
    # CLI mapping → D7 wording, exit 1.
    msg, code = format_error(ei.value, data_dir=data_dir)
    assert code == 1
    assert "Topic 'workout' already exists at" in msg
