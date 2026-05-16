"""Unit tests for ``remory.transcripts``."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest

from remory import transcripts


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )


def _user_event(text: str, *, ts: str = "2026-05-09T09:30:00.000Z") -> dict[str, Any]:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "uuid": "u",
        "timestamp": ts,
        "sessionId": "s",
        "isSidechain": False,
    }


def _assistant_event(
    blocks: list[dict[str, Any]],
    *,
    ts: str = "2026-05-09T09:30:01.000Z",
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": blocks},
        "uuid": "a",
        "timestamp": ts,
        "sessionId": "s",
        "isSidechain": False,
    }


def test_encode_cwd_for_claude_replaces_slash_and_dot_with_dash() -> None:
    assert transcripts.encode_cwd_for_claude(Path("/home/user/x.y")) == "-home-user-x-y"


def test_iter_events_skips_malformed_lines_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "t.jsonl"
    valid = _user_event("ok")
    path.write_text(
        json.dumps(valid) + "\n" + "not json {\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="remory.transcripts"):
        events = list(transcripts.iter_events(path))
    assert len(events) == 1
    assert any("malformed JSON" in rec.getMessage() for rec in caplog.records)


def test_to_markdown_exact_bytes_for_canonical_two_event_transcript(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [
            _user_event("hello"),
            _assistant_event([{"type": "text", "text": "hi there"}]),
        ],
    )
    expected = "**You:** hello\n\n**Remory:** hi there\n"
    assert transcripts.to_markdown(path) == expected


def test_to_markdown_role_labels_are_exact_bytes(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [
            _user_event("u"),
            _assistant_event([{"type": "text", "text": "a"}]),
        ],
    )
    out = transcripts.to_markdown(path)
    assert "**You:** " in out
    assert "**Remory:** " in out
    # Negative checks: no other label spellings sneak in.
    assert "**you:**" not in out.lower().replace("**you:** ", "")
    assert "**user:**" not in out.lower()


def test_to_markdown_skips_sidechain_events(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    sidechain = _user_event("sidechain content")
    sidechain["isSidechain"] = True
    _write_jsonl(
        path,
        [
            sidechain,
            _user_event("real"),
            _assistant_event([{"type": "text", "text": "yes"}]),
        ],
    )
    out = transcripts.to_markdown(path)
    assert "sidechain content" not in out
    assert "real" in out


def test_to_markdown_skips_empty_text_blocks(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [
            _user_event("u"),
            _assistant_event(
                [
                    {"type": "text", "text": "   "},
                    {"type": "text", "text": "real text"},
                    {"type": "text", "text": ""},
                ]
            ),
        ],
    )
    out = transcripts.to_markdown(path)
    assert "**Remory:** real text\n" in out
    # No double-blank artefacts from empty blocks.
    assert "\n\n\n" not in out.rstrip("\n") + "\n"


def test_to_markdown_does_not_escape_markdown_special_characters(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [
            _user_event("**bold** and _under_"),
            _assistant_event([{"type": "text", "text": "ok"}]),
        ],
    )
    out = transcripts.to_markdown(path)
    assert "**bold**" in out
    assert "_under_" in out


def test_to_markdown_does_not_wrap_in_code_fences(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [
            _user_event("u"),
            _assistant_event([{"type": "text", "text": "```python\nprint(1)\n```"}]),
        ],
    )
    out = transcripts.to_markdown(path)
    # Exactly two fences (the original ones), not four.
    assert out.count("```") == 2


def test_to_markdown_returns_empty_string_for_transcript_with_no_user_or_assistant_events(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "system", "message": {"content": "boot"}, "uuid": "x"},
            {"type": "summary", "message": {"content": "tldr"}, "uuid": "y"},
        ],
    )
    assert transcripts.to_markdown(path) == ""


def test_to_markdown_renders_multi_block_assistant_content_joined_with_double_newline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [
            _user_event("u"),
            _assistant_event(
                [
                    {"type": "text", "text": "first"},
                    {"type": "text", "text": "second"},
                ]
            ),
        ],
    )
    out = transcripts.to_markdown(path)
    assert "**Remory:** first\n\nsecond\n" in out


def test_to_markdown_renders_tool_use_block_as_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [
            _user_event("u"),
            _assistant_event(
                [
                    {"type": "text", "text": "thinking"},
                    {"type": "tool_use", "name": "Read", "input": {}},
                ]
            ),
        ],
    )
    out = transcripts.to_markdown(path)
    assert "<!-- tool: Read -->" in out


def test_locate_latest_returns_newest_jsonl_by_mtime(tmp_path: Path) -> None:
    cwd = tmp_path / "work"
    cwd.mkdir()
    project_dir = transcripts.project_dir_for(cwd)
    project_dir.mkdir(parents=True, exist_ok=True)
    older = project_dir / "older.jsonl"
    newer = project_dir / "newer.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    time.sleep(0.01)
    newer.write_text("{}\n", encoding="utf-8")
    # Force ordering on filesystems with coarse mtime.
    import os as _os

    _os.utime(older, (older.stat().st_atime, older.stat().st_mtime - 5))
    located = transcripts.locate_latest(cwd)
    assert located is not None
    assert located.name == "newer.jsonl"


def test_locate_latest_returns_None_when_project_dir_absent(tmp_path: Path) -> None:
    cwd = tmp_path / "no_such_topic"
    cwd.mkdir()
    # Point claude_projects_dir at a temp-only home so absent dir is honest.
    import os as _os

    _os.environ.pop("FAKE_CLAUDE_HOME", None)
    fake_home = tmp_path / "claude_home"
    _os.environ["FAKE_CLAUDE_HOME"] = str(fake_home)
    try:
        assert transcripts.locate_latest(cwd) is None
    finally:
        _os.environ.pop("FAKE_CLAUDE_HOME", None)
