"""End-to-end wizard tests (Phase 6 — claude-driven flow; plan §11.2).

Uses ``fake_claude`` ``wizard_interactive`` mode (a single subprocess
fork/exec for each chat call). The tests pin the user-visible outcomes:

- Valid answers → topic dirs + about-me.md on disk.
- Malformed-then-valid → one repair round, COMMIT runs.
- Malformed twice → recovery dir written, exception raised, no COMMIT.
- Preflight refusal → no chat call, doctor pointer on stderr.
- Subagent killed (exit-code 1) → no COMMIT, no recovery dir.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.console import Console

from remory import paths
from remory.backends.claude_code import ClaudeCodeBackend
from remory.ui import CheckResult, CheckStatus
from remory.wizard import (
    WizardPreflightError,
    WizardSubagentFailedError,
)
from remory.wizard import _orchestrator as orch_mod
from remory.wizard._orchestrator import run_wizard

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only fake-claude binary and locking",
)


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


@pytest.fixture
def fake_claude_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Prepend a ``claude`` shim (symlink to fake_claude) to PATH.

    Mirrors :func:`tests.conftest.fake_claude_on_path` but yields the
    ``bin/`` dir directly — wizard tests don't need a separate claude
    home (the fake interactive mode does not write to it).
    """
    fakes_dir = Path(__file__).parent.parent / "fakes"
    fake = fakes_dir / "fake_claude"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "claude"
    shim.symlink_to(fake)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    yield bin_dir


@pytest.fixture
def wizard_run_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Pin the wizard run directory under tmp_path so the test can inspect it.

    Monkeypatches :class:`tempfile.TemporaryDirectory` inside the
    orchestrator to a context manager wrapping our owned path. The
    ``FAKE_CLAUDE_WIZARD_RUN_DIR`` env var is set to the same path so
    the fake's wizard mode writes there.
    """
    run_dir = tmp_path / "wizard-run"

    class _Owned:
        def __init__(self) -> None:
            self._path = run_dir

        def __enter__(self) -> str:
            self._path.mkdir(parents=True, exist_ok=True)
            return str(self._path)

        def __exit__(self, *exc_info: object) -> None:
            del exc_info

    def factory(prefix: str = "") -> _Owned:
        del prefix
        return _Owned()

    monkeypatch.setattr(orch_mod, "TemporaryDirectory", factory)
    monkeypatch.setenv("FAKE_CLAUDE_WIZARD_RUN_DIR", str(run_dir))
    yield run_dir


@pytest.fixture
def patched_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force preflight to succeed.

    The integration tests are about the flow from chat call onward; the
    auth probe is unit-tested in the doctor's own suite. We bypass it
    here so the fake claude doesn't have to satisfy an auth round-trip
    AND a wizard round-trip in the same invocation.
    """
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


def _quiet_console() -> Console:
    import io

    return Console(file=io.StringIO(), color_system=None, no_color=True)


_VALID_ANSWERS = json.dumps(
    {
        "version": 1,
        "name": "Sam",
        "chosen_topics": ["workout"],
        "knobs_by_topic": {"workout": {"tone": "warm", "strictness": "balanced"}},
        "wish": "stop forgetting",
    }
)


def test_wizard_e2e_writes_topic_dirs_and_about_me_when_fake_claude_produces_valid_json(
    isolated_xdg: Path,
    fake_claude_on_path: Path,
    wizard_run_dir: Path,
    patched_preflight: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = isolated_xdg / "data"
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "wizard_interactive")
    monkeypatch.setenv("FAKE_CLAUDE_WIZARD_ANSWERS", _VALID_ANSWERS)
    monkeypatch.setenv("FAKE_CLAUDE_WIZARD_LETTER", "Hi Sam. I'll remember.\n")

    run_wizard(
        backend_factory=ClaudeCodeBackend,
        console=_quiet_console(),
        data_dir=data_dir,
    )

    assert (data_dir / "topics" / "workout").is_dir()
    about_me = paths.about_me_file(data_dir).read_text(encoding="utf-8")
    assert about_me.startswith("Hi Sam. I'll remember.")
    assert "name: Sam\n" in about_me


def test_wizard_e2e_retries_once_and_succeeds_when_fake_claude_first_writes_malformed_then_valid(
    isolated_xdg: Path,
    fake_claude_on_path: Path,
    wizard_run_dir: Path,
    patched_preflight: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = isolated_xdg / "data"
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "wizard_interactive")
    monkeypatch.setenv("FAKE_CLAUDE_WIZARD_ANSWERS", _VALID_ANSWERS)
    monkeypatch.setenv("FAKE_CLAUDE_WIZARD_LETTER", "second-try letter\n")
    monkeypatch.setenv("FAKE_CLAUDE_WIZARD_FAIL", "write_malformed_json_once")

    run_wizard(
        backend_factory=ClaudeCodeBackend,
        console=_quiet_console(),
        data_dir=data_dir,
    )

    assert (data_dir / "topics" / "workout").is_dir()
    about_me = paths.about_me_file(data_dir).read_text(encoding="utf-8")
    assert about_me.startswith("second-try letter")


def test_wizard_e2e_writes_recovery_and_exits_nonzero_when_fake_claude_malformed_twice(
    isolated_xdg: Path,
    fake_claude_on_path: Path,
    wizard_run_dir: Path,
    patched_preflight: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = isolated_xdg / "data"
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "wizard_interactive")
    monkeypatch.setenv("FAKE_CLAUDE_WIZARD_ANSWERS", _VALID_ANSWERS)
    monkeypatch.setenv("FAKE_CLAUDE_WIZARD_FAIL", "write_malformed_json_twice")

    with pytest.raises(WizardSubagentFailedError) as ei:
        run_wizard(
            backend_factory=ClaudeCodeBackend,
            console=_quiet_console(),
            data_dir=data_dir,
        )

    assert ei.value.recovery_dir is not None
    assert ei.value.recovery_dir.is_dir()
    assert (ei.value.recovery_dir / "validation-error.txt").exists()
    # No COMMIT happened.
    assert not (data_dir / "topics" / "workout").exists()


def test_wizard_e2e_refuses_to_run_when_preflight_fails(
    isolated_xdg: Path,
    fake_claude_on_path: Path,
    wizard_run_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Preflight refusal: no chat call (no env required), stderr carries doctor pointer."""
    data_dir = isolated_xdg / "data"
    monkeypatch.setattr(
        orch_mod,
        "_check_claude_binary",
        lambda: CheckResult(
            id="claude_binary",
            status=CheckStatus.FAIL,
            label="claude binary",
            detail="(test)",
        ),
    )

    with pytest.raises(WizardPreflightError):
        run_wizard(
            backend_factory=ClaudeCodeBackend,
            console=_quiet_console(),
            data_dir=data_dir,
        )

    captured = capsys.readouterr()
    assert "remory doctor" in captured.err
    assert not (data_dir / "topics").exists() or not any((data_dir / "topics").iterdir())


def test_wizard_e2e_leaves_no_files_when_user_kills_subagent(
    isolated_xdg: Path,
    fake_claude_on_path: Path,
    wizard_run_dir: Path,
    patched_preflight: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fake_claude FAIL=preflight_exit_nonzero → subagent returns 1 → no COMMIT, no recovery."""
    data_dir = isolated_xdg / "data"
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "wizard_interactive")
    monkeypatch.setenv("FAKE_CLAUDE_WIZARD_FAIL", "preflight_exit_nonzero")

    with pytest.raises(WizardSubagentFailedError) as ei:
        run_wizard(
            backend_factory=ClaudeCodeBackend,
            console=_quiet_console(),
            data_dir=data_dir,
        )

    # Subagent failed before producing anything; no recovery dir.
    assert ei.value.recovery_dir is None
    assert not (data_dir / "topics").exists() or not any((data_dir / "topics").iterdir())
    assert not paths.about_me_file(data_dir).exists()
    recovery_root = data_dir / ".remory" / "wizard-recovery"
    assert not recovery_root.exists()
