"""Integration tests exercising the chat/headless seams against the fake binary."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from remory import transcripts
from remory.backends.claude_code import ClaudeCodeBackend

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fake binary")


def test_chat_flow_writes_locatable_parseable_transcript_with_exact_markdown(
    fake_claude_on_path: tuple[Path],
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "topic"
    cwd.mkdir()
    backend = ClaudeCodeBackend()
    result = backend.chat(cwd=cwd)
    assert result.exit_code == 0
    assert result.transcript_path is not None
    assert result.transcript_path.exists()
    assert result.session_id == "fake-session-0001"

    markdown = transcripts.to_markdown(result.transcript_path)
    expected = "**You:** hello\n\n**Remory:** hi there\n"
    assert markdown == expected


def test_chat_flow_returns_no_transcript_when_fake_interactive_fails(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_INTERACTIVE_FAIL", "1")
    backend = ClaudeCodeBackend()
    result = backend.chat(cwd=tmp_path)
    assert result.exit_code != 0
    assert result.transcript_path is None


def test_headless_round_trip_with_agent_flag_via_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "success_text")
    monkeypatch.setenv("FAKE_CLAUDE_TEXT", "extracted!")
    dump = tmp_path / "argv.dump"
    monkeypatch.setenv("FAKE_CLAUDE_ARGV_DUMP", str(dump))

    backend = ClaudeCodeBackend()
    result = backend.headless(prompt="extract this", agent="extractor", json_output=True)
    assert result.text == "extracted!"

    argv = dump.read_text(encoding="utf-8").splitlines()
    # The fake records argv[0] (its own path) plus all flags.
    assert "-p" in argv
    p_idx = argv.index("-p")
    assert argv[p_idx + 1] == "extract this"
    assert "--agent" in argv
    a_idx = argv.index("--agent")
    assert argv[a_idx + 1] == "extractor"
    assert "--output-format" in argv
    f_idx = argv.index("--output-format")
    assert argv[f_idx + 1] == "json"
    # Agent flag comes after the prompt; output-format after the agent.
    assert p_idx < a_idx < f_idx
