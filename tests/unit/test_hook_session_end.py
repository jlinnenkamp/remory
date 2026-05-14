"""Unit tests for ``remory _hook session-end`` (plan §11.1 verbatim names).

Pin D1 (chat_cmd owns the threshold nudge; SessionEnd never prints) via
``test_session_end_hook_never_prints_threshold_nudge_when_pending_crosses_threshold``.

Pin D4 (load-bearing wizard-transcript skip) via
``test_session_end_hook_returns_no_topic_when_cwd_is_data_dir_root_not_topic_subdir``.

Each test seeds REMORY_DATA_DIR via the ``isolated_xdg`` fixture so the
hook resolves the same data dir as the test fixtures.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remory import paths
from remory.commands.init_cmd import run_init
from remory.hooks import session_end
from remory.hooks.session_end import (
    SessionEndInput,
    main,
    run,
)
from remory.locking import topic_lock
from remory.raw import RawFrontmatter, RawSource, RawStatus, list_raw, write_raw
from remory.topic import read_meta


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def _write_jsonl(path: Path, *, session_id: str, body: str = "hello") -> None:
    user = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": body}]},
        "uuid": "u-1",
        "timestamp": "2026-05-09T09:30:00.000Z",
        "sessionId": session_id,
        "isSidechain": False,
    }
    assistant = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "ack"}],
        },
        "uuid": "a-1",
        "timestamp": "2026-05-09T09:30:01.000Z",
        "sessionId": session_id,
        "isSidechain": False,
    }
    path.write_text(json.dumps(user) + "\n" + json.dumps(assistant) + "\n", encoding="utf-8")


def _seed_workout(isolated_xdg: Path) -> Path:
    run_init(topic_name="workout", schema_name="workout")
    return isolated_xdg / "data" / "topics" / "workout"


# ---------------------------------------------------------------------------
# no_topic — D4 load-bearing
# ---------------------------------------------------------------------------


def test_session_end_hook_returns_no_topic_when_cwd_not_under_topics_root(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    """cwd outside <data_dir>/topics/ → no_topic, no side effects."""
    del isolated_xdg
    outcome = run(
        SessionEndInput(
            cwd=tmp_path / "outside",
            session_id="sess-x",
            transcript_path=None,
        )
    )
    assert outcome.status == "no_topic"
    assert outcome.raw_path is None


def test_session_end_hook_returns_no_topic_when_cwd_is_data_dir_root_not_topic_subdir(
    isolated_xdg: Path,
) -> None:
    """D4 — wizard-transcript skip mechanism.

    The wizard launches `claude --agent wizard` with cwd=data_dir (NOT a
    topic dir). The hook must return ``no_topic`` for cwd at the data
    root so the wizard's transcript is not captured as a raw entry.
    """
    # Ensure the data dir exists.
    data_dir = isolated_xdg / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    outcome = run(
        SessionEndInput(
            cwd=data_dir,  # data root, NOT data/topics/<name>/
            session_id="sess-wizard",
            transcript_path=None,
        )
    )
    assert outcome.status == "no_topic"
    assert outcome.raw_path is None


# ---------------------------------------------------------------------------
# deferred_locked — ADR-0002 chat-as-canonical
# ---------------------------------------------------------------------------


def test_session_end_hook_returns_deferred_locked_when_chat_parent_holds_lock(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    """The chat parent holds the lock → hook defers silently."""
    topic_dir = _seed_workout(isolated_xdg)
    transcript_path = tmp_path / "session-x.jsonl"
    _write_jsonl(transcript_path, session_id="sess-x")
    # Note: in-process lock is non-reentrant; the in-process lock guard
    # in the same process triggers the "deferred_locked" branch via
    # the is_locked() probe.
    with topic_lock(topic_dir, timeout=0.0):
        outcome = run(
            SessionEndInput(
                cwd=topic_dir,
                session_id="sess-x",
                transcript_path=transcript_path,
            )
        )
    assert outcome.status == "deferred_locked"
    assert outcome.raw_path is None


# ---------------------------------------------------------------------------
# duplicate_skip — session_id idempotency floor
# ---------------------------------------------------------------------------


def test_session_end_hook_returns_duplicate_skip_when_session_id_already_recorded(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    topic_dir = _seed_workout(isolated_xdg)
    # Pre-write an entry with the session id we'll re-supply.
    with topic_lock(topic_dir, timeout=0.0):
        fm = RawFrontmatter(
            created=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
            source=RawSource.CHAT,
            status=RawStatus.PENDING,
            session_id="sess-dup",
            duration_seconds=0,
        )
        write_raw(topic_dir, frontmatter=fm, body="prior body")

    transcript_path = tmp_path / "sess-dup.jsonl"
    _write_jsonl(transcript_path, session_id="sess-dup")

    outcome = run(
        SessionEndInput(
            cwd=topic_dir,
            session_id="sess-dup",
            transcript_path=transcript_path,
        )
    )
    assert outcome.status == "duplicate_skip"
    # Still only one entry with that session_id.
    entries = list_raw(topic_dir)
    assert sum(1 for e in entries if e.frontmatter.session_id == "sess-dup") == 1


# ---------------------------------------------------------------------------
# wrote — happy path
# ---------------------------------------------------------------------------


def test_session_end_hook_writes_raw_entry_when_unlocked_and_no_duplicate(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    topic_dir = _seed_workout(isolated_xdg)
    transcript_path = tmp_path / "sess-new.jsonl"
    _write_jsonl(transcript_path, session_id="sess-new")

    outcome = run(
        SessionEndInput(
            cwd=topic_dir,
            session_id="sess-new",
            transcript_path=transcript_path,
        )
    )
    assert outcome.status == "wrote"
    assert outcome.raw_path is not None
    assert outcome.raw_path.exists()
    entries = list_raw(topic_dir)
    assert any(e.frontmatter.session_id == "sess-new" for e in entries)


# ---------------------------------------------------------------------------
# empty_transcript — no silent data loss
# ---------------------------------------------------------------------------


def test_session_end_hook_returns_empty_transcript_and_logs_warning_when_markdown_empty(
    isolated_xdg: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    topic_dir = _seed_workout(isolated_xdg)
    # An "empty" transcript: a JSONL file with no user/assistant events,
    # just a stray system event. to_markdown returns "" for this.
    transcript_path = tmp_path / "empty.jsonl"
    transcript_path.write_text(
        json.dumps({"type": "system", "uuid": "s-1"}) + "\n", encoding="utf-8"
    )
    with caplog.at_level("WARNING", logger="remory.hooks.session_end"):
        outcome = run(
            SessionEndInput(
                cwd=topic_dir,
                session_id="sess-empty",
                transcript_path=transcript_path,
            )
        )
    assert outcome.status == "empty_transcript"
    # WARNING-level emission is the receipt.
    assert any(
        r.levelname == "WARNING" and "empty" in r.getMessage()
        for r in caplog.records
        if r.name == "remory.hooks.session_end"
    )


# ---------------------------------------------------------------------------
# error — never raises
# ---------------------------------------------------------------------------


def test_session_end_hook_returns_error_without_raising_when_meta_yaml_unparseable(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    topic_dir = _seed_workout(isolated_xdg)
    # Corrupt meta.yaml so the load_topic call inside run() will raise.
    paths.meta_file(topic_dir).write_text(": :: bad yaml ::", encoding="utf-8")
    transcript_path = tmp_path / "sess-err.jsonl"
    _write_jsonl(transcript_path, session_id="sess-err")

    outcome = run(
        SessionEndInput(
            cwd=topic_dir,
            session_id="sess-err",
            transcript_path=transcript_path,
        )
    )
    assert outcome.status == "error"
    assert outcome.raw_path is None


# ---------------------------------------------------------------------------
# log discipline — feedback_log_omit_prompt_adjacent_fields
# ---------------------------------------------------------------------------


def test_session_end_hook_logs_omit_transcript_bodies_and_stderr_tail(
    isolated_xdg: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The error log MUST whitelist exception_type/topic/session_id only.

    No transcript body, no stderr_tail, no prompt echo. Pin per memory
    `feedback_log_omit_prompt_adjacent_fields`.
    """
    topic_dir = _seed_workout(isolated_xdg)
    paths.meta_file(topic_dir).write_text(": :: bad yaml ::", encoding="utf-8")
    transcript_path = tmp_path / "sess-leak.jsonl"
    _write_jsonl(
        transcript_path,
        session_id="sess-leak",
        body="this body must not appear in logs",
    )

    with caplog.at_level("WARNING", logger="remory.hooks.session_end"):
        run(
            SessionEndInput(
                cwd=topic_dir,
                session_id="sess-leak",
                transcript_path=transcript_path,
            )
        )

    error_rows = [
        r
        for r in caplog.records
        if r.name == "remory.hooks.session_end" and r.levelname == "WARNING"
    ]
    assert error_rows, "expected at least one WARNING log row from the error path"
    target = error_rows[-1]
    # Whitelisted keys present.
    assert getattr(target, "exception_type", None) == "TopicMetaError"
    assert getattr(target, "session_id", None) == "sess-leak"
    # Forbidden keys absent.
    assert not hasattr(target, "stderr_tail")
    assert not hasattr(target, "transcript")
    assert not hasattr(target, "body")
    # And the transcript body string never leaks into the formatted message.
    formatted = target.getMessage()
    assert "this body must not appear in logs" not in formatted


# ---------------------------------------------------------------------------
# meta.yaml bump on write
# ---------------------------------------------------------------------------


def test_session_end_hook_bumps_pending_count_and_last_chat_on_write(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    topic_dir = _seed_workout(isolated_xdg)
    before = read_meta(topic_dir)
    transcript_path = tmp_path / "sess-bump.jsonl"
    _write_jsonl(transcript_path, session_id="sess-bump")

    run(
        SessionEndInput(
            cwd=topic_dir,
            session_id="sess-bump",
            transcript_path=transcript_path,
        )
    )

    after = read_meta(topic_dir)
    assert after.pending_count == before.pending_count + 1
    assert after.total_entries == before.total_entries + 1
    assert after.last_chat is not None


# ---------------------------------------------------------------------------
# main() always exits 0
# ---------------------------------------------------------------------------


def test_session_end_hook_main_exits_zero_always_even_on_error(
    isolated_xdg: Path,
) -> None:
    del isolated_xdg
    # Even with completely empty stdin, main() must return 0.
    code = main(argv=[], stdin=io.StringIO(""))
    assert code == 0
    # And with a malformed JSON body, still 0.
    code2 = main(argv=[], stdin=io.StringIO("not json {"))
    assert code2 == 0


# ---------------------------------------------------------------------------
# D1 — chat_cmd owns the threshold nudge; SessionEnd never prints.
# ---------------------------------------------------------------------------


def test_session_end_hook_never_prints_threshold_nudge_when_pending_crosses_threshold(
    isolated_xdg: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """D1 pin: the hook MUST NOT print the threshold suggestion.

    chat_cmd owns the nudge (ADR-0007). Crossing the threshold in a
    SessionEnd-only path must produce no stdout from this hook.
    """
    topic_dir = _seed_workout(isolated_xdg)
    # Set pending_count to threshold-1 so crossing happens on this write.
    from remory.topic import write_meta

    meta = read_meta(topic_dir)
    threshold = 3  # workout schema sleep.trigger_threshold
    new_meta = meta.model_copy(update={"pending_count": threshold - 1})
    with topic_lock(topic_dir, timeout=0.0):
        write_meta(topic_dir, new_meta)

    transcript_path = tmp_path / "sess-nudge.jsonl"
    _write_jsonl(transcript_path, session_id="sess-nudge")

    # Drain pre-existing capsys content first so we measure only the hook.
    capsys.readouterr()

    outcome = run(
        SessionEndInput(
            cwd=topic_dir,
            session_id="sess-nudge",
            transcript_path=transcript_path,
        )
    )
    assert outcome.status == "wrote"
    # Confirm pending_count actually crossed.
    after = read_meta(topic_dir)
    assert after.pending_count >= threshold

    out = capsys.readouterr().out
    err = capsys.readouterr().err
    # Hook produces no stdout/stderr; only logs.
    assert "remory sleep" not in out
    assert "remory sleep" not in err
    assert "pending entries" not in out
    assert "pending entries" not in err


# ---------------------------------------------------------------------------
# Permissive stdin parsing (alias keys)
# ---------------------------------------------------------------------------


def test_session_end_hook_main_accepts_both_session_id_and_sessionId_keys(
    isolated_xdg: Path,
) -> None:
    """Permissive parsing per plan D9: ``session_id`` or ``sessionId``."""
    del isolated_xdg
    # Use the camelCase alias only; main() should not crash.
    payload = {
        "sessionId": "sess-camel",
        "transcriptPath": "/nonexistent.jsonl",
        "cwd": "/nonexistent",
    }
    code = main(argv=[], stdin=io.StringIO(json.dumps(payload)))
    assert code == 0


# ---------------------------------------------------------------------------
# Module-level smoke: callable surface present
# ---------------------------------------------------------------------------


def test_session_end_module_exposes_run_main_and_models() -> None:
    assert callable(session_end.run)
    assert callable(session_end.main)
    assert session_end.SessionEndInput is not None
    assert session_end.SessionEndOutcome is not None
