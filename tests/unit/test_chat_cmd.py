"""Tests for ``remory chat`` (D1 fork+wait, D2 SessionEnd, D6 preconditions)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from remory import paths, transcripts
from remory.backends.base import ChatResult
from remory.cli.errors import TopicIncompleteError, TopicMissingError
from remory.commands.chat_cmd import run_chat
from remory.commands.init_cmd import run_init
from remory.locking import is_locked, topic_lock
from remory.raw import RawSource, RawStatus, list_raw
from remory.topic import read_meta
from tests.fakes.fake_backend import FakeBackend


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def _write_jsonl(path: Path, *, session_id: str) -> None:
    """Write a tiny canonical user/assistant transcript to ``path``."""
    import json

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
            "content": [{"type": "text", "text": "hello!"}],
        },
        "uuid": "a-1",
        "timestamp": "2026-05-09T09:30:01.000Z",
        "sessionId": session_id,
        "isSidechain": False,
    }
    path.write_text(json.dumps(user) + "\n" + json.dumps(assistant) + "\n", encoding="utf-8")


def _seed_topic(isolated_xdg: Path) -> Path:
    """Initialise a real topic via the init stub; return its dir."""
    run_init(topic_name="workout", schema_name="workout")
    return isolated_xdg / "data" / "topics" / "workout"


# ---------------------------------------------------------------------------
# D6 — three precondition cases
# ---------------------------------------------------------------------------


def test_run_chat_raises_topic_missing_when_topic_does_not_exist(
    isolated_xdg: Path,
) -> None:
    del isolated_xdg
    with pytest.raises(TopicMissingError) as ei:
        run_chat(
            topic_name="nope",
            continue_session=False,
            backend_factory=FakeBackend,
        )
    assert ei.value.name == "nope"


def test_run_chat_raises_topic_incomplete_when_meta_yaml_missing(
    isolated_xdg: Path,
) -> None:
    topic_dir = _seed_topic(isolated_xdg)
    paths.meta_file(topic_dir).unlink()
    with pytest.raises(TopicIncompleteError):
        run_chat(
            topic_name="workout",
            continue_session=False,
            backend_factory=FakeBackend,
        )


def test_run_chat_raises_topic_incomplete_when_state_md_missing(
    isolated_xdg: Path,
) -> None:
    topic_dir = _seed_topic(isolated_xdg)
    paths.state_file(topic_dir).unlink()
    with pytest.raises(TopicIncompleteError):
        run_chat(
            topic_name="workout",
            continue_session=False,
            backend_factory=FakeBackend,
        )


# ---------------------------------------------------------------------------
# D1 — fork+wait lock invariant
# ---------------------------------------------------------------------------


def test_run_chat_holds_lock_during_subprocess_and_releases_on_return(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent must hold the topic lock across the subprocess and
    through the post-exit raw-write; on return, the lock is released.
    """
    topic_dir = _seed_topic(isolated_xdg)

    # Drive the fake backend's chat() to write a transcript to the
    # FAKE_CLAUDE_HOME projects dir we control. We bypass the subprocess
    # path entirely by using FakeBackend; it returns a ChatResult and
    # the parent's transcript_path/locator runs normally.
    home = isolated_xdg / "claude_home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("FAKE_CLAUDE_HOME", str(home))
    encoded = transcripts.encode_cwd_for_claude(topic_dir.resolve())
    pdir = home / "projects" / encoded
    pdir.mkdir(parents=True)
    transcript_path = pdir / "session-aaa.jsonl"
    _write_jsonl(transcript_path, session_id="session-aaa")

    chat_result = ChatResult(
        exit_code=0,
        session_id="session-aaa",
        transcript_path=transcript_path,
        duration_seconds=1.0,
        cwd=topic_dir,
    )
    backend = FakeBackend(chat_result=chat_result)

    # While running, an external acquirer would be locked out — the
    # FakeBackend's chat() doesn't fork a real subprocess so we can't
    # observe mid-run; verify post-return that lock is free and that the
    # raw-write happened.
    run_chat(
        topic_name="workout",
        continue_session=False,
        backend_factory=lambda: backend,
    )

    # Lock released on return.
    assert is_locked(topic_dir) is False

    # Raw entry written.
    entries = list_raw(topic_dir, status=RawStatus.PENDING)
    assert len(entries) == 1
    assert entries[0].frontmatter.session_id == "session-aaa"
    assert entries[0].frontmatter.source is RawSource.CHAT


def test_run_chat_raises_lock_busy_when_another_process_holds_lock(
    isolated_xdg: Path,
) -> None:
    from remory.locking import LockBusyError

    topic_dir = _seed_topic(isolated_xdg)
    backend = FakeBackend(
        chat_result=ChatResult(
            exit_code=0,
            session_id=None,
            transcript_path=None,
            duration_seconds=0.0,
            cwd=topic_dir,
        )
    )
    with topic_lock(topic_dir, timeout=0.0), pytest.raises(LockBusyError):
        run_chat(
            topic_name="workout",
            continue_session=False,
            backend_factory=lambda: backend,
        )


# ---------------------------------------------------------------------------
# Threshold suggestion
# ---------------------------------------------------------------------------


def test_run_chat_prints_threshold_suggestion_at_or_above_trigger(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    topic_dir = _seed_topic(isolated_xdg)
    # Bump pending_count to trigger - 1 so the next chat tips it over.
    from remory.topic import write_meta

    meta = read_meta(topic_dir)
    threshold = 3  # workout schema sleep.trigger_threshold
    new_meta = meta.model_copy(update={"pending_count": threshold - 1})
    with topic_lock(topic_dir, timeout=0.0):
        write_meta(topic_dir, new_meta)

    home = isolated_xdg / "claude_home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("FAKE_CLAUDE_HOME", str(home))
    encoded = transcripts.encode_cwd_for_claude(topic_dir.resolve())
    pdir = home / "projects" / encoded
    pdir.mkdir(parents=True)
    transcript_path = pdir / "session-bbb.jsonl"
    _write_jsonl(transcript_path, session_id="session-bbb")

    chat_result = ChatResult(
        exit_code=0,
        session_id="session-bbb",
        transcript_path=transcript_path,
        duration_seconds=2.0,
        cwd=topic_dir,
    )
    backend = FakeBackend(chat_result=chat_result)
    run_chat(
        topic_name="workout",
        continue_session=False,
        backend_factory=lambda: backend,
    )
    out = capsys.readouterr().out
    assert "remory sleep workout" in out


def test_run_chat_resume_flag_passes_through_to_backend(
    isolated_xdg: Path,
) -> None:
    topic_dir = _seed_topic(isolated_xdg)
    backend = FakeBackend(
        chat_result=ChatResult(
            exit_code=1,  # non-zero → no raw-write, just check the call kwargs
            session_id=None,
            transcript_path=None,
            duration_seconds=0.0,
            cwd=topic_dir,
        )
    )
    run_chat(
        topic_name="workout",
        continue_session=True,
        backend_factory=lambda: backend,
    )
    assert backend.chat_calls == [{"cwd": topic_dir, "resume": True}]
