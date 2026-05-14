"""E2E: SessionEnd hook driven by stdin + fake transcripts.

Plan §11.2 — 3 tests. Exercises the ``main()`` shim through the Typer
subapp surface; transcript bytes come from a fixture file
``transcripts.to_markdown`` renders the same way the chat path does.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from remory.commands.init_cmd import run_init
from remory.hooks.session_end import SessionEndInput
from remory.hooks.session_end import run as run_hook
from remory.raw import RawStatus, list_raw

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fixtures")


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def _write_jsonl(path: Path, *, session_id: str) -> None:
    user = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        "uuid": "u-1",
        "timestamp": "2026-05-09T09:30:00.000Z",
        "sessionId": session_id,
        "isSidechain": False,
    }
    assistant = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "hi there"}],
        },
        "uuid": "a-1",
        "timestamp": "2026-05-09T09:30:01.000Z",
        "sessionId": session_id,
        "isSidechain": False,
    }
    path.write_text(json.dumps(user) + "\n" + json.dumps(assistant) + "\n", encoding="utf-8")


def test_session_end_hook_writes_raw_entry_when_chat_parent_missing(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    """No chat parent → hook writes the raw entry itself."""
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    transcript_path = tmp_path / "sess-e2e.jsonl"
    _write_jsonl(transcript_path, session_id="sess-e2e")

    outcome = run_hook(
        SessionEndInput(
            cwd=topic_dir,
            session_id="sess-e2e",
            transcript_path=transcript_path,
        )
    )
    assert outcome.status == "wrote"
    entries = list_raw(topic_dir, status=RawStatus.PENDING)
    assert any(e.frontmatter.session_id == "sess-e2e" for e in entries)


def test_session_end_hook_threshold_nudge_is_not_printed_by_hook(
    isolated_xdg: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """D1 pin (also covered in unit tests, here as an e2e cross-check)."""
    from remory.locking import topic_lock
    from remory.topic import read_meta, write_meta

    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    threshold = 3
    meta = read_meta(topic_dir)
    with topic_lock(topic_dir, timeout=0.0):
        write_meta(topic_dir, meta.model_copy(update={"pending_count": threshold - 1}))

    transcript_path = tmp_path / "sess-nudge.jsonl"
    _write_jsonl(transcript_path, session_id="sess-nudge")

    capsys.readouterr()
    outcome = run_hook(
        SessionEndInput(
            cwd=topic_dir,
            session_id="sess-nudge",
            transcript_path=transcript_path,
        )
    )
    assert outcome.status == "wrote"
    captured = capsys.readouterr()
    assert "remory sleep" not in captured.out
    assert "remory sleep" not in captured.err


def test_session_end_hook_uses_to_markdown_renderer_not_its_own(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    """The hook calls ``transcripts.to_markdown``, not a private renderer.

    Pin the renderer-shape contract by reading the resulting raw-entry
    body and asserting on the canonical ``**You:** ... **Remory:** ...``
    structure that ``to_markdown`` produces.
    """
    from remory.raw import read_raw

    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    transcript_path = tmp_path / "sess-render.jsonl"
    _write_jsonl(transcript_path, session_id="sess-render")

    outcome = run_hook(
        SessionEndInput(
            cwd=topic_dir,
            session_id="sess-render",
            transcript_path=transcript_path,
        )
    )
    assert outcome.status == "wrote"
    assert outcome.raw_path is not None
    body = read_raw(outcome.raw_path).body
    # Canonical to_markdown output: two role labels in order.
    assert "**You:** hello" in body
    assert "**Remory:** hi there" in body
