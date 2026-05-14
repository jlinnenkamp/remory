"""E2E coordination: chat and SessionEnd do not produce duplicate entries.

Plan §11.2 — 4 tests. The chat surface is the canonical writer
(ADR-0002); the hook defers under the lock and skips duplicates via the
session-id scan.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remory import transcripts
from remory.backends.base import ChatResult
from remory.commands.chat_cmd import run_chat
from remory.commands.init_cmd import run_init
from remory.hooks.session_end import SessionEndInput
from remory.hooks.session_end import run as run_hook
from remory.locking import topic_lock
from remory.raw import RawFrontmatter, RawSource, RawStatus, list_raw, write_raw
from tests.fakes.fake_backend import FakeBackend

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
        "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        "uuid": "u-1",
        "timestamp": "2026-05-09T09:30:00.000Z",
        "sessionId": session_id,
        "isSidechain": False,
    }
    assistant = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
        },
        "uuid": "a-1",
        "timestamp": "2026-05-09T09:30:01.000Z",
        "sessionId": session_id,
        "isSidechain": False,
    }
    path.write_text(json.dumps(user) + "\n" + json.dumps(assistant) + "\n", encoding="utf-8")


def test_chat_canonical_writes_raw_entry_under_lock(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_chat writes the raw entry; the hook does not need to fire."""
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"

    home = isolated_xdg / "claude_home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("FAKE_CLAUDE_HOME", str(home))
    encoded = transcripts.encode_cwd_for_claude(topic_dir.resolve())
    pdir = home / "projects" / encoded
    pdir.mkdir(parents=True)
    transcript_path = pdir / "sess-canon.jsonl"
    _write_jsonl(transcript_path, session_id="sess-canon")

    backend = FakeBackend(
        chat_result=ChatResult(
            exit_code=0,
            session_id="sess-canon",
            transcript_path=transcript_path,
            duration_seconds=1.0,
            cwd=topic_dir,
        )
    )
    run_chat(
        topic_name="workout",
        continue_session=False,
        backend_factory=lambda: backend,
    )
    entries = list_raw(topic_dir, status=RawStatus.PENDING)
    assert sum(1 for e in entries if e.frontmatter.session_id == "sess-canon") == 1


def test_session_end_hook_defers_when_chat_parent_still_holds_lock(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    """The hook returns ``deferred_locked`` while the parent holds the lock."""
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    transcript_path = tmp_path / "sess-defer.jsonl"
    _write_jsonl(transcript_path, session_id="sess-defer")

    with topic_lock(topic_dir, timeout=0.0):
        outcome = run_hook(
            SessionEndInput(
                cwd=topic_dir,
                session_id="sess-defer",
                transcript_path=transcript_path,
            )
        )
        assert outcome.status == "deferred_locked"


def test_session_end_hook_skips_duplicate_when_session_id_already_on_disk(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    """The hook's session-id scan is the belt-and-suspenders dedup floor."""
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"

    with topic_lock(topic_dir, timeout=0.0):
        write_raw(
            topic_dir,
            frontmatter=RawFrontmatter(
                created=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
                source=RawSource.CHAT,
                status=RawStatus.PENDING,
                session_id="sess-prior",
                duration_seconds=0,
            ),
            body="prior body",
        )

    transcript_path = tmp_path / "sess-prior.jsonl"
    _write_jsonl(transcript_path, session_id="sess-prior")

    outcome = run_hook(
        SessionEndInput(
            cwd=topic_dir,
            session_id="sess-prior",
            transcript_path=transcript_path,
        )
    )
    assert outcome.status == "duplicate_skip"


def test_no_double_raw_entry_when_chat_and_hook_both_fire(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sequence: chat writes first, then the hook fires post-release.

    The hook's session-id scan must skip the entry. Total raw count for
    the session_id stays at 1.
    """
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"

    home = isolated_xdg / "claude_home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("FAKE_CLAUDE_HOME", str(home))
    encoded = transcripts.encode_cwd_for_claude(topic_dir.resolve())
    pdir = home / "projects" / encoded
    pdir.mkdir(parents=True)
    transcript_path = pdir / "sess-both.jsonl"
    _write_jsonl(transcript_path, session_id="sess-both")

    backend = FakeBackend(
        chat_result=ChatResult(
            exit_code=0,
            session_id="sess-both",
            transcript_path=transcript_path,
            duration_seconds=1.0,
            cwd=topic_dir,
        )
    )
    run_chat(
        topic_name="workout",
        continue_session=False,
        backend_factory=lambda: backend,
    )
    # Now the hook fires (lock is already released by run_chat).
    outcome = run_hook(
        SessionEndInput(
            cwd=topic_dir,
            session_id="sess-both",
            transcript_path=transcript_path,
        )
    )
    assert outcome.status == "duplicate_skip"
    entries = list_raw(topic_dir, status=RawStatus.PENDING)
    assert sum(1 for e in entries if e.frontmatter.session_id == "sess-both") == 1
