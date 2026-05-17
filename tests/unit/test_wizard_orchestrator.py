"""Wizard orchestrator tests for the Phase 6 claude-driven flow (plan §11.1).

The orchestrator is the contract-bearing surface:
- preflight refusal raises ``WizardPreflightError`` without entering chat.
- happy path: subagent writes valid files → COMMIT runs.
- repair round: first parse fails, second succeeds → COMMIT runs.
- second failure: dump_recovery + raise.
- subagent exits non-zero → no COMMIT.
- ``data_dir`` is threaded through to the COMMIT step unchanged.

The tests do NOT use ``fake_claude``; they drive a custom in-process
backend that records ``chat`` calls and writes the run-dir files on
demand. ``TemporaryDirectory`` is monkeypatched so the test owns the
run-dir path.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType

import pytest
from rich.console import Console

from remory import paths
from remory.backends.base import (
    BackendInvocationError,
    ChatResult,
    HeadlessMeta,
    HeadlessResult,
    HealthReport,
)
from remory.wizard import (
    WizardAnswerParseError,
    WizardPreflightError,
    WizardSubagentFailedError,
)
from remory.wizard import _orchestrator as orch_mod
from remory.wizard._orchestrator import run_wizard

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only locking under test",
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _quiet_console() -> Console:
    return Console(file=io.StringIO(), color_system=None, no_color=True)


def _valid_answers_payload(*, topics: tuple[str, ...] = ("workout",)) -> dict[str, object]:
    knobs = {t: {"tone": "warm", "strictness": "balanced"} for t in topics}
    return {
        "version": 1,
        "name": "Sam",
        "chosen_topics": list(topics),
        "knobs_by_topic": knobs,
        "wish": "stop forgetting",
    }


class _AuthOKBackend:
    """A backend stub whose ``health_check``/``headless`` both look authenticated.

    ``chat`` is overridden by subclasses. ``headless`` returns a canned
    success envelope so the doctor's auth probe (used in preflight)
    passes without touching the network.
    """

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
        raise NotImplementedError  # subclasses override

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
        # Minimal canned envelope: doctor's _check_claude_auth only
        # inspects truthiness + session_id, so any HeadlessResult will do.
        return HeadlessResult(
            text="ok",
            session_id="fake-auth-session",
            duration_ms=1,
            num_turns=1,
            stop_reason="end_turn",
            meta=HeadlessMeta(raw_envelope=None),
        )

    def health_check(self) -> HealthReport:
        # Not used in preflight (the doctor probes via _check_claude_binary
        # + _check_claude_auth directly), but stubbed for completeness.
        return HealthReport(
            binary_present=True,
            binary_path=Path("/usr/bin/claude"),
            version="fake-claude 0.0.1",
            authenticated=True,
            notes=(),
        )


class _PreflightFailBackend(_AuthOKBackend):
    """Backend whose auth probe raises an auth-shaped error."""

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
        raise BackendInvocationError(
            "claude exited 1",
            exit_code=1,
            stderr_tail="please login first",
        )


class _ScriptedChatBackend(_AuthOKBackend):
    """Backend whose ``chat`` calls run scripted side effects.

    Each step in ``script`` is a callable taking ``(run_dir)`` and
    returning a ``ChatResult``. The orchestrator's run_dir is staged
    before the first chat call (we stash it in the test via the
    TemporaryDirectory monkeypatch).
    """

    def __init__(self, script: Iterable[object], run_dir_holder: dict[str, Path]) -> None:
        super().__init__()
        self._script: list[object] = list(script)
        self._run_dir_holder = run_dir_holder

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
        if not self._script:
            raise AssertionError("ScriptedChatBackend.chat: script exhausted")
        step = self._script.pop(0)
        run_dir = self._run_dir_holder.get("path")
        if run_dir is None:
            raise AssertionError("run_dir not staged before chat call")
        if not callable(step):
            raise AssertionError(f"script step is not callable: {step!r}")
        result = step(run_dir, cwd, resume, agent)
        if not isinstance(result, ChatResult):
            raise AssertionError(f"script step returned {type(result).__name__}, not ChatResult")
        return result


# ---------------------------------------------------------------------------
# TemporaryDirectory monkeypatch helper
# ---------------------------------------------------------------------------


class _OwnedTempDir:
    """Stand-in for ``tempfile.TemporaryDirectory`` whose path the test owns."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def __enter__(self) -> str:
        self._path.mkdir(parents=True, exist_ok=True)
        return str(self._path)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Leave the dir in place — the test asserts on its contents in
        # failure-path tests. tmp_path cleans up at the session level.
        del exc_type, exc, tb


@contextmanager
def _owned_tempdir_factory(
    monkeypatch: pytest.MonkeyPatch,
    run_dir_path: Path,
    run_dir_holder: dict[str, Path],
) -> Iterator[None]:
    def factory(prefix: str = "") -> _OwnedTempDir:
        del prefix
        run_dir_holder["path"] = run_dir_path
        return _OwnedTempDir(run_dir_path)

    monkeypatch.setattr(orch_mod, "TemporaryDirectory", factory)
    yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


@pytest.fixture
def patched_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force preflight to succeed for orchestrator-only tests.

    The orchestrator's preflight imports the doctor's helpers; in the
    unit-test layer we don't want to depend on a real ``claude`` binary
    or auth probe. Tests that exercise preflight refusal use the
    ``_PreflightFailBackend`` plus this patch's inverse below.
    """
    from remory.ui import CheckResult, CheckStatus

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


def _write_valid(run_dir: Path, *, topics: tuple[str, ...] = ("workout",)) -> None:
    (run_dir / "answers.json").write_text(
        json.dumps(_valid_answers_payload(topics=topics)), encoding="utf-8"
    )
    (run_dir / "letter.md").write_text("Hi Sam. I'll keep what you bring.\n", encoding="utf-8")


def _make_chat_step(
    *,
    side_effect: object,
    exit_code: int = 0,
) -> object:
    def step(
        run_dir: Path,
        cwd: Path,
        resume: bool,
        agent: str | None,
    ) -> ChatResult:
        del cwd, resume, agent
        if callable(side_effect):
            side_effect(run_dir)
        return ChatResult(
            exit_code=exit_code,
            session_id="fake-session",
            transcript_path=None,
            duration_seconds=0.0,
            cwd=run_dir,
        )

    return step


def test_run_wizard_skips_subagent_and_raises_when_preflight_fails(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Preflight refusal: no chat call, stderr carries doctor pointer, raises."""
    data_dir = isolated_xdg / "data"
    # Patch preflight to fail — we don't go through _PreflightFailBackend
    # because the binary check fires first.
    from remory.ui import CheckResult, CheckStatus

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

    backend = _ScriptedChatBackend(script=[], run_dir_holder={})

    with pytest.raises(WizardPreflightError):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            data_dir=data_dir,
        )

    assert backend.chat_calls == []
    captured = capsys.readouterr()
    assert "remory doctor" in captured.err


def test_run_wizard_commits_when_subagent_writes_valid_files(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_preflight: None,  # fixture applies patches as a side effect
) -> None:
    """Happy path: chat writes files → COMMIT runs → topic dir + about-me.md exist."""
    data_dir = isolated_xdg / "data"
    run_dir = isolated_xdg / "run"
    holder: dict[str, Path] = {}
    backend = _ScriptedChatBackend(
        script=[_make_chat_step(side_effect=lambda rd: _write_valid(rd))],
        run_dir_holder=holder,
    )

    with _owned_tempdir_factory(monkeypatch, run_dir, holder):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            data_dir=data_dir,
        )

    # One chat call with agent="wizard" and cwd=data_dir.
    assert len(backend.chat_calls) == 1
    assert backend.chat_calls[0]["agent"] == "wizard"
    assert backend.chat_calls[0]["cwd"] == data_dir

    # Topic dir + about-me.md exist.
    assert (data_dir / "topics" / "workout").is_dir()
    about_me = paths.about_me_file(data_dir).read_text(encoding="utf-8")
    assert about_me.startswith("Hi Sam.")


def test_run_wizard_retries_once_when_first_answers_malformed_then_commits(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_preflight: None,
) -> None:
    """Repair round: first chat writes malformed answers, second writes valid."""
    data_dir = isolated_xdg / "data"
    run_dir = isolated_xdg / "run"
    holder: dict[str, Path] = {}

    def write_malformed(rd: Path) -> None:
        (rd / "answers.json").write_text("{not json", encoding="utf-8")
        (rd / "letter.md").write_text("partial letter\n", encoding="utf-8")

    backend = _ScriptedChatBackend(
        script=[
            _make_chat_step(side_effect=write_malformed),
            _make_chat_step(side_effect=lambda rd: _write_valid(rd)),
        ],
        run_dir_holder=holder,
    )

    with _owned_tempdir_factory(monkeypatch, run_dir, holder):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            data_dir=data_dir,
        )

    # Two chat calls: first resume=False, second resume=True.
    assert len(backend.chat_calls) == 2
    assert backend.chat_calls[0]["resume"] is False
    assert backend.chat_calls[1]["resume"] is True
    # COMMIT succeeded.
    assert (data_dir / "topics" / "workout").is_dir()


def test_run_wizard_first_chat_initial_prompt_contains_run_dir_path_and_speak_first(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_preflight: None,
) -> None:
    """The first-attempt chat() carries an initial_prompt that names the run
    directory and instructs the subagent to speak first. Without this,
    interactive `claude --agent wizard` drops the user at an empty prompt
    waiting for input (real bug fixed in this commit)."""
    data_dir = isolated_xdg / "data"
    run_dir = isolated_xdg / "run"
    holder: dict[str, Path] = {}
    backend = _ScriptedChatBackend(
        script=[_make_chat_step(side_effect=lambda rd: _write_valid(rd))],
        run_dir_holder=holder,
    )

    with _owned_tempdir_factory(monkeypatch, run_dir, holder):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            data_dir=data_dir,
        )

    assert len(backend.chat_calls) == 1
    initial_prompt = backend.chat_calls[0]["initial_prompt"]
    assert isinstance(initial_prompt, str)
    # Path appears at least once so the subagent knows where its
    # manifest + schemas live.
    assert str(run_dir) in initial_prompt
    # Kick-off language: the wizard must take the first turn.
    assert "Speak first" in initial_prompt


def test_run_wizard_repair_chat_initial_prompt_points_at_repair_prompt_file(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_preflight: None,
) -> None:
    """When the first attempt fails parse, the repair chat() carries an
    initial_prompt that points at <run_dir>/repair_prompt.txt so the
    subagent reads the validation error even if --resume drops the agent
    context."""
    data_dir = isolated_xdg / "data"
    run_dir = isolated_xdg / "run"
    holder: dict[str, Path] = {}

    def write_malformed(rd: Path) -> None:
        (rd / "answers.json").write_text("{not json", encoding="utf-8")
        (rd / "letter.md").write_text("partial letter\n", encoding="utf-8")

    backend = _ScriptedChatBackend(
        script=[
            _make_chat_step(side_effect=write_malformed),
            _make_chat_step(side_effect=lambda rd: _write_valid(rd)),
        ],
        run_dir_holder=holder,
    )

    with _owned_tempdir_factory(monkeypatch, run_dir, holder):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            data_dir=data_dir,
        )

    assert len(backend.chat_calls) == 2
    repair_prompt = backend.chat_calls[1]["initial_prompt"]
    assert isinstance(repair_prompt, str)
    assert str(run_dir / "repair_prompt.txt") in repair_prompt
    assert "answers.json" in repair_prompt


def test_run_wizard_dumps_recovery_and_raises_when_second_attempt_fails(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_preflight: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two-strike parse fail: recovery dir written, exception raised, no COMMIT."""
    data_dir = isolated_xdg / "data"
    run_dir = isolated_xdg / "run"
    holder: dict[str, Path] = {}

    def write_malformed(rd: Path) -> None:
        (rd / "answers.json").write_text("{nope", encoding="utf-8")
        (rd / "letter.md").write_text("nope letter\n", encoding="utf-8")

    backend = _ScriptedChatBackend(
        script=[
            _make_chat_step(side_effect=write_malformed),
            _make_chat_step(side_effect=write_malformed),
        ],
        run_dir_holder=holder,
    )

    with (
        _owned_tempdir_factory(monkeypatch, run_dir, holder),
        pytest.raises((WizardSubagentFailedError, WizardAnswerParseError)),
    ):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            data_dir=data_dir,
        )

    # No topic dir created.
    assert not (data_dir / "topics" / "workout").exists()
    # Recovery dir written.
    recovery_root = data_dir / ".remory" / "wizard-recovery"
    assert recovery_root.is_dir()
    recovery_dirs = list(recovery_root.iterdir())
    assert len(recovery_dirs) >= 1
    # Stderr carries the recovery-message template.
    captured = capsys.readouterr()
    assert "wizard couldn't produce valid answers" in captured.err


def test_run_wizard_does_not_enter_commit_when_subagent_exits_nonzero(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_preflight: None,
) -> None:
    """Subagent exit-code != 0 on first call → no COMMIT, raises."""
    data_dir = isolated_xdg / "data"
    run_dir = isolated_xdg / "run"
    holder: dict[str, Path] = {}
    backend = _ScriptedChatBackend(
        script=[_make_chat_step(side_effect=lambda rd: None, exit_code=42)],
        run_dir_holder=holder,
    )

    with (
        _owned_tempdir_factory(monkeypatch, run_dir, holder),
        pytest.raises(WizardSubagentFailedError),
    ):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            data_dir=data_dir,
        )

    assert not (data_dir / "topics" / "workout").exists()
    assert not paths.about_me_file(data_dir).exists()


def test_run_wizard_passes_data_dir_through_to_commit_unchanged(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_preflight: None,
) -> None:
    """The data_dir kwarg threads to commit unchanged (no canonicalisation)."""
    custom_data_dir = isolated_xdg / "alt-data-dir"
    run_dir = isolated_xdg / "run"
    holder: dict[str, Path] = {}
    backend = _ScriptedChatBackend(
        script=[_make_chat_step(side_effect=lambda rd: _write_valid(rd))],
        run_dir_holder=holder,
    )

    with _owned_tempdir_factory(monkeypatch, run_dir, holder):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            data_dir=custom_data_dir,
        )

    # The custom data dir was used: topic dir and about-me.md land there.
    assert (custom_data_dir / "topics" / "workout").is_dir()
    assert paths.about_me_file(custom_data_dir).exists()
    # chat was called with that cwd.
    assert backend.chat_calls[0]["cwd"] == custom_data_dir
