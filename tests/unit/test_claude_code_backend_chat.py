"""Unit tests for ``ClaudeCodeBackend.chat``."""

from __future__ import annotations

from pathlib import Path

import pytest

from remory.backends.claude_code import ClaudeCodeBackend


def test_chat_locates_transcript_post_exit(
    fake_claude_on_path: tuple[Path],
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "work"
    cwd.mkdir()
    backend = ClaudeCodeBackend()
    result = backend.chat(cwd=cwd)
    assert result.exit_code == 0
    assert result.transcript_path is not None
    assert result.transcript_path.exists()


def test_chat_resume_passes_resume_flag(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_EXPECT_RESUME", "1")
    cwd = tmp_path / "work"
    cwd.mkdir()
    backend = ClaudeCodeBackend()
    result = backend.chat(cwd=cwd, resume=True)
    assert result.exit_code == 0


def test_chat_returns_no_transcript_when_fake_fails(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_INTERACTIVE_FAIL", "1")
    cwd = tmp_path / "work"
    cwd.mkdir()
    backend = ClaudeCodeBackend()
    result = backend.chat(cwd=cwd)
    assert result.exit_code != 0
    assert result.transcript_path is None
