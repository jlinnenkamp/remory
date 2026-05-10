"""``remory chat`` — interactive Claude Code session for a topic.

D1 lock mechanics: fork+wait. The Python parent acquires
``topic_lock(topic_dir, timeout=0.0)`` before spawning the claude
subprocess; the subprocess does NOT hold the lock. The parent holds the
lock continuously across the subprocess and through the post-exit
raw-write. Release happens on context-manager exit. No release/re-acquire.

D2 SessionEnd coordination: ``chat_cmd`` is the canonical writer in
v0.1. It does not branch on hook presence; the hook (Phase 6) is
responsible for deferring via ``locking.is_locked`` + session_id scan.
This file ships once and does not change between Phase 4 and Phase 6.

D6 preconditions: three cases (missing / incomplete / complete). Missing
raises :class:`TopicMissingError`; incomplete raises
:class:`TopicIncompleteError`; only "complete" topics proceed to chat.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from remory import config as cfgmod
from remory import paths, transcripts
from remory.backends.base import Backend
from remory.backends.claude_code import ClaudeCodeBackend
from remory.cli.errors import TopicIncompleteError, TopicMissingError
from remory.locking import topic_lock
from remory.raw import RawFrontmatter, RawSource, RawStatus, write_raw
from remory.state import StateParseError, read_state
from remory.topic import TopicMetaError, load_topic, write_meta

__all__ = ["run_chat"]

_log = logging.getLogger("remory.commands.chat")


def _default_backend_factory() -> Backend:
    return ClaudeCodeBackend()


def _existing_topics(topics_root: Path) -> tuple[str, ...]:
    if not topics_root.is_dir():
        return ()
    return tuple(sorted(p.name for p in topics_root.iterdir() if p.is_dir()))


def _check_preconditions(topic_dir: Path, *, topics_root: Path) -> None:
    """Implement D6's three cases.

    Raises :class:`TopicMissingError` for case 1, :class:`TopicIncompleteError`
    for case 2; returns silently for case 3 ("complete").
    """
    if not topic_dir.exists():
        raise TopicMissingError(topic_dir.name, existing_topics=_existing_topics(topics_root))

    state_path = paths.state_file(topic_dir)
    meta_path = paths.meta_file(topic_dir)

    if not meta_path.exists():
        raise TopicIncompleteError(topic_dir.name, "meta.yaml missing")
    if not state_path.exists():
        raise TopicIncompleteError(topic_dir.name, "state.md missing")

    # Parse-shape check: if either is unparseable, treat as incomplete.
    try:
        load_topic(topic_dir)
    except TopicMetaError as exc:
        raise TopicIncompleteError(topic_dir.name, f"meta.yaml unparseable: {exc}") from exc
    try:
        read_state(state_path)
    except StateParseError as exc:
        raise TopicIncompleteError(topic_dir.name, f"state.md unparseable: {exc}") from exc


def run_chat(
    *,
    topic_name: str,
    continue_session: bool,
    backend_factory: Callable[[], Backend] | None = None,
) -> None:
    """Implement the chat command per D1+D2+D6.

    Args:
        topic_name: the topic to chat about.
        continue_session: when True, passes ``resume=True`` to the backend.
        backend_factory: zero-arg callable returning a :class:`Backend`;
            test seam.

    Returns nothing on success; raises an error on precondition failure
    or backend failure (CLI maps).
    """
    factory = backend_factory if backend_factory is not None else _default_backend_factory

    cfg = cfgmod.load_config()
    data_dir = cfgmod.resolve_data_dir(cfg)
    topics_root = data_dir / "topics"
    topic_dir = topics_root / topic_name

    _check_preconditions(topic_dir, topics_root=topics_root)

    backend = factory()

    # D1: acquire the lock once and hold it across subprocess + post-write.
    with topic_lock(topic_dir, timeout=0.0):
        result = backend.chat(cwd=topic_dir, resume=continue_session)
        # On clean exit, capture the transcript and write a raw entry.
        # On non-zero exit, do nothing — the user saw the failure inline.
        if result.exit_code != 0:
            _log.warning(
                "chat: backend exited with non-zero code; skipping raw-write",
                extra={
                    "topic": topic_name,
                    "exit_code": result.exit_code,
                    "session_id": result.session_id,
                },
            )
            return

        transcript_path = result.transcript_path
        session_id = result.session_id
        if transcript_path is None or session_id is None:
            _log.warning(
                "chat: no transcript or session_id; skipping raw-write",
                extra={
                    "topic": topic_name,
                    "transcript_path": (
                        str(transcript_path) if transcript_path is not None else None
                    ),
                    "session_id": session_id,
                },
            )
            return

        try:
            body = transcripts.to_markdown(transcript_path)
        except transcripts.TranscriptParseError as exc:
            _log.warning(
                "chat: could not read transcript %s: %s; skipping raw-write",
                transcript_path,
                exc,
            )
            return

        if not body.strip():
            _log.info(
                "chat: empty transcript; skipping raw-write",
                extra={"topic": topic_name, "session_id": session_id},
            )
            return

        now = datetime.now(UTC)
        duration = max(0, int(result.duration_seconds))
        fm = RawFrontmatter(
            created=now,
            source=RawSource.CHAT,
            status=RawStatus.PENDING,
            session_id=session_id,
            duration_seconds=duration,
        )
        raw_path = write_raw(topic_dir, frontmatter=fm, body=body)

        # Bump pending_count + last_chat in meta.yaml.
        topic = load_topic(topic_dir)
        new_meta = topic.meta.model_copy(
            update={
                "last_chat": now,
                "pending_count": topic.meta.pending_count + 1,
                "total_entries": topic.meta.total_entries + 1,
            }
        )
        write_meta(topic_dir, new_meta)

        sys.stdout.write(f"Captured chat as {raw_path}\n")
        threshold = topic.schema.sleep.trigger_threshold
        if new_meta.pending_count >= threshold:
            sys.stdout.write(
                f"You're at {new_meta.pending_count} pending entries — "
                f"`remory sleep {topic_name}` whenever it feels right.\n"
            )
