"""Unit tests for ``ClaudeCodeBackend.headless`` and ``health_check``."""

from __future__ import annotations

from pathlib import Path

import pytest

from remory.backends.base import (
    BackendInvocationError,
    BackendNotFoundError,
    BackendOutputError,
    BackendTimeoutError,
)
from remory.backends.claude_code import ClaudeCodeBackend


def test_headless_returns_text_from_envelope(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "success_text")
    monkeypatch.setenv("FAKE_CLAUDE_TEXT", "extracted!")
    backend = ClaudeCodeBackend()
    result = backend.headless(prompt="hi", json_output=True)
    assert result.text == "extracted!"
    assert result.session_id == "fake-session-0001"
    assert result.duration_ms == 42


def test_headless_passes_resume_flag_via_chat_method(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
    tmp_path: Path,
) -> None:
    """``chat(resume=True)`` must invoke argv with ``--resume`` (not
    ``--continue``). Asserted via fake's FAKE_CLAUDE_EXPECT_RESUME guard."""
    monkeypatch.setenv("FAKE_CLAUDE_EXPECT_RESUME", "1")
    backend = ClaudeCodeBackend()
    cwd = tmp_path / "work"
    cwd.mkdir()
    result = backend.chat(cwd=cwd, resume=True)
    assert result.exit_code == 0


def test_headless_argv_includes_agent_flag_when_provided(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
    tmp_path: Path,
) -> None:
    dump = tmp_path / "argv.dump"
    monkeypatch.setenv("FAKE_CLAUDE_ARGV_DUMP", str(dump))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "success")
    backend = ClaudeCodeBackend()
    backend.headless(prompt="extract this", agent="extractor", json_output=True)
    argv = dump.read_text(encoding="utf-8").splitlines()
    assert "--agent" in argv
    agent_idx = argv.index("--agent")
    assert argv[agent_idx + 1] == "extractor"


def test_headless_raises_BackendOutputError_on_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "malformed_json")
    backend = ClaudeCodeBackend()
    with pytest.raises(BackendOutputError):
        backend.headless(prompt="hi", json_output=True)


def test_headless_raises_BackendInvocationError_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "nonzero_exit")
    monkeypatch.setenv("FAKE_CLAUDE_EXIT_CODE", "3")
    backend = ClaudeCodeBackend()
    with pytest.raises(BackendInvocationError) as exc_info:
        backend.headless(prompt="hi", json_output=True)
    assert exc_info.value.exit_code == 3


def test_headless_raises_BackendTimeoutError_on_hang(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    monkeypatch.setenv("FAKE_CLAUDE_HANG_SECONDS", "30")
    backend = ClaudeCodeBackend()
    with pytest.raises(BackendTimeoutError):
        backend.headless(prompt="hi", json_output=True, timeout_seconds=1)


def test_headless_raises_BackendOutputError_on_is_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "is_error_envelope")
    backend = ClaudeCodeBackend()
    with pytest.raises(BackendOutputError):
        backend.headless(prompt="hi", json_output=True)


def test_headless_raises_BackendNotFoundError_when_binary_missing() -> None:
    backend = ClaudeCodeBackend(binary="nope-claude-not-here")
    with pytest.raises(BackendNotFoundError):
        backend.headless(prompt="hi", json_output=True)


def test_health_check_reports_version_and_unknown_auth(
    fake_claude_on_path: tuple[Path],
) -> None:
    backend = ClaudeCodeBackend()
    report = backend.health_check()
    assert report.binary_present is True
    assert report.version is not None
    assert report.authenticated is None
    assert "auth not probed" in report.notes
