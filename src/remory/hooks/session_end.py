"""Claude Code SessionEnd hook — capture transcript as a raw entry.

Implements the policy table in consolidated plan §8.1. The hook is
invoked by claude after any session ends; it does not block claude.

Coordination with ``remory chat`` is governed by ADR-0002: chat is the
canonical writer; the hook defers when the chat parent still holds the
topic lock, and uses a session-id scan as a belt-and-suspenders
idempotency floor.

D1: the hook NEVER prints the threshold nudge. The nudge is owned by
``remory chat`` only (ADR-0007). Users invoking ``claude`` directly will
see the nudge on their next ``remory chat``.

D4: the wizard-transcript skip relies on the ``no_topic`` branch — when
the wizard launches ``claude --agent wizard`` with ``cwd=eff_data_dir``,
the hook fires with ``cwd`` not under any topic dir and exits silently.
Do NOT remove the strict "direct child of <data_dir>/topics/" check
without re-reading ADR-0002.

Logging discipline (``feedback_log_omit_prompt_adjacent_fields``): the
error path logs only ``exception_type``, ``topic`` (str), ``session_id``.
No ``stderr_tail``, no transcript echo, no message bodies.

Empty-transcript discipline (``feedback_no_silent_data_loss``): an empty
transcript surfaces a WARNING log AND returns ``empty_transcript`` so the
caller has a record; we do not write an empty raw entry, but we do not
swallow the event either.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from remory import config as cfgmod
from remory import paths, transcripts
from remory.locking import LockBusyError, is_locked, topic_lock
from remory.raw import RawFrontmatter, RawSource, RawStatus, list_raw, read_raw, write_raw
from remory.topic import load_topic, write_meta

__all__ = [
    "SessionEndInput",
    "SessionEndOutcome",
    "main",
    "run",
]

_log = logging.getLogger("remory.hooks.session_end")


# ---------------------------------------------------------------------------
# IO models
# ---------------------------------------------------------------------------


SessionEndStatus = Literal[
    "wrote",
    "deferred_locked",
    "duplicate_skip",
    "no_topic",
    "empty_transcript",
    "error",
]


@dataclass(frozen=True)
class SessionEndInput:
    """Inputs to :func:`run` decoded from the claude hook payload."""

    cwd: Path
    session_id: str | None
    transcript_path: Path | None


@dataclass(frozen=True)
class SessionEndOutcome:
    """Outcome of :func:`run`. ``status`` is the policy-table branch taken."""

    status: SessionEndStatus
    raw_path: Path | None
    note: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_data_dir() -> Path:
    """Resolve the effective data directory; mirrors the CLI's helper."""
    try:
        cfg = cfgmod.load_config()
    except cfgmod.ConfigError:
        return paths.data_dir()
    return cfgmod.resolve_data_dir(cfg)


def _resolve_topic_dir(cwd: Path, data_dir: Path) -> Path | None:
    """Return the topic dir iff ``cwd`` is a direct child of ``<data_dir>/topics/``.

    Resolves symlinks on both sides before matching. D4: this is the
    wizard-transcript skip mechanism — the wizard launches with
    ``cwd=eff_data_dir``, which is NOT under ``topics/``, so the hook
    returns ``no_topic`` and exits silently.
    """
    topics_root = (data_dir / "topics").resolve()
    try:
        resolved_cwd = cwd.resolve(strict=False)
    except OSError:
        return None
    if resolved_cwd.parent != topics_root:
        return None
    if not resolved_cwd.is_dir():
        return None
    return resolved_cwd


def _has_session_id_on_disk(topic_dir: Path, session_id: str) -> bool:
    """Scan raw/ for any entry with matching ``session_id`` in frontmatter."""
    for entry_path in (p.path for p in list_raw(topic_dir)):
        try:
            entry = read_raw(entry_path)
        except Exception:  # pragma: no cover - defensive; list_raw already validates
            continue
        if entry.frontmatter.session_id == session_id:
            return True
    return False


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


def run(payload: SessionEndInput) -> SessionEndOutcome:
    """Execute the SessionEnd policy table; never raises.

    Steps (plan §8.1):

    1. Resolve topic_dir from cwd; if not a direct child of
       ``<data_dir>/topics/``, return ``no_topic``.
    2. ``is_locked(topic_dir)`` → ``deferred_locked`` with DEBUG log.
    3. Acquire ``topic_lock(timeout=0.0)``; on :class:`LockBusyError` →
       ``deferred_locked``.
    4. Under lock: scan ``list_raw`` for matching ``session_id`` →
       ``duplicate_skip``.
    5. ``transcripts.to_markdown``; if empty/whitespace →
       ``empty_transcript`` + WARNING log.
    6. ``write_raw`` + bump ``meta.yaml`` ``pending_count`` +
       ``last_chat`` → ``wrote``.

    On any exception: return ``error``; do NOT raise. Log WARNING with
    ``exception_type``, ``topic`` (str), ``session_id`` only.
    """
    try:
        data_dir = _resolve_data_dir()
        topic_dir = _resolve_topic_dir(payload.cwd, data_dir)
        if topic_dir is None:
            return SessionEndOutcome(
                status="no_topic",
                raw_path=None,
                note=f"cwd {payload.cwd} not under {data_dir}/topics/<name>/",
            )

        topic_name = topic_dir.name

        if is_locked(topic_dir):
            _log.debug(
                "session_end: topic lock held; deferring to chat owner",
                extra={"topic": topic_name, "session_id": payload.session_id},
            )
            return SessionEndOutcome(
                status="deferred_locked",
                raw_path=None,
                note=f"topic {topic_name!r} lock held by another holder",
            )

        try:
            with topic_lock(topic_dir, timeout=0.0):
                # Step 4: duplicate scan.
                if payload.session_id is not None and _has_session_id_on_disk(
                    topic_dir, payload.session_id
                ):
                    _log.debug(
                        "session_end: session_id already recorded; skipping",
                        extra={"topic": topic_name, "session_id": payload.session_id},
                    )
                    return SessionEndOutcome(
                        status="duplicate_skip",
                        raw_path=None,
                        note=f"session_id {payload.session_id!r} already on disk",
                    )

                # Step 5: render transcript.
                if payload.transcript_path is None:
                    _log.warning(
                        "session_end: no transcript path supplied",
                        extra={"topic": topic_name, "session_id": payload.session_id},
                    )
                    return SessionEndOutcome(
                        status="empty_transcript",
                        raw_path=None,
                        note="transcript_path was None",
                    )

                body = transcripts.to_markdown(payload.transcript_path)
                if not body.strip():
                    _log.warning(
                        "session_end: transcript rendered empty; skipping raw-write",
                        extra={"topic": topic_name, "session_id": payload.session_id},
                    )
                    return SessionEndOutcome(
                        status="empty_transcript",
                        raw_path=None,
                        note=f"transcript at {payload.transcript_path} rendered empty",
                    )

                # Step 6: write_raw + bump meta.
                now = datetime.now(UTC)
                session_id_for_write = payload.session_id or "unknown"
                fm = RawFrontmatter(
                    created=now,
                    source=RawSource.CHAT,
                    status=RawStatus.PENDING,
                    session_id=session_id_for_write,
                    duration_seconds=0,
                )
                raw_path = write_raw(topic_dir, frontmatter=fm, body=body)

                topic = load_topic(topic_dir)
                new_meta = topic.meta.model_copy(
                    update={
                        "last_chat": now,
                        "pending_count": topic.meta.pending_count + 1,
                        "total_entries": topic.meta.total_entries + 1,
                    }
                )
                write_meta(topic_dir, new_meta)

                _log.info(
                    "session_end: wrote raw entry",
                    extra={"topic": topic_name, "session_id": payload.session_id},
                )
                # D1 pin: NEVER print the threshold nudge here. The chat
                # surface owns that. Do not add a sys.stdout.write here.
                return SessionEndOutcome(
                    status="wrote",
                    raw_path=raw_path,
                    note=f"wrote {raw_path}",
                )
        except LockBusyError:
            _log.debug(
                "session_end: lock acquire raced; deferring",
                extra={
                    "topic": topic_dir.name,
                    "session_id": payload.session_id,
                },
            )
            return SessionEndOutcome(
                status="deferred_locked",
                raw_path=None,
                note=f"lock race on topic {topic_dir.name!r}",
            )

    except Exception as exc:
        # Whitelisted fields only (memory feedback_log_omit_prompt_adjacent_fields).
        _log.warning(
            "session_end: unexpected error; exiting silently",
            extra={
                "exception_type": type(exc).__name__,
                "topic": str(payload.cwd),
                "session_id": payload.session_id,
            },
        )
        return SessionEndOutcome(
            status="error",
            raw_path=None,
            note=f"unexpected error: {type(exc).__name__}",
        )


# ---------------------------------------------------------------------------
# CLI shim
# ---------------------------------------------------------------------------


def _parse_stdin(stdin: io.TextIOBase | None) -> dict[str, object]:
    """Parse the claude hook payload from stdin (JSON).

    Permissive: accepts both ``session_id`` and ``sessionId``, both
    ``transcript_path`` and ``transcriptPath``, both ``cwd`` and
    ``current_working_directory``. Returns an empty dict on any parse
    failure — the caller falls back to env/argv.
    """
    stream = stdin if stdin is not None else sys.stdin
    try:
        raw = stream.read()
    except (OSError, ValueError):
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return cast("dict[str, object]", parsed)


def _coerce_optional_str(payload: dict[str, object], *keys: str) -> str | None:
    """Return the first non-empty string value among ``keys`` in ``payload``."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _build_input(payload: dict[str, object]) -> SessionEndInput:
    """Build a :class:`SessionEndInput` from a parsed hook payload + env fallbacks.

    Pinned keys we depend on:

    - ``cwd`` / ``current_working_directory`` — defaults to ``os.getcwd()``.
    - ``session_id`` / ``sessionId`` — optional.
    - ``transcript_path`` / ``transcriptPath`` — optional.
    """
    cwd_str = _coerce_optional_str(payload, "cwd", "current_working_directory")
    cwd = Path(cwd_str) if cwd_str else Path(os.getcwd())

    session_id = _coerce_optional_str(payload, "session_id", "sessionId")

    transcript_path_str = _coerce_optional_str(payload, "transcript_path", "transcriptPath")
    transcript_path = Path(transcript_path_str) if transcript_path_str else None

    return SessionEndInput(
        cwd=cwd,
        session_id=session_id,
        transcript_path=transcript_path,
    )


def main(argv: list[str] | None = None, stdin: io.TextIOBase | None = None) -> int:
    """Thin shim invoked from the ``remory _hook session-end`` Typer subapp.

    Reads the claude hook payload from stdin, builds a
    :class:`SessionEndInput`, calls :func:`run`, and ALWAYS returns 0.
    Hooks must never block claude — even on a programming bug, we exit
    successfully so the user's chat session ends cleanly.
    """
    del argv  # accepted for symmetry with pre_tool_use.main; unused.
    payload = _parse_stdin(stdin)
    inp = _build_input(payload)
    run(inp)
    return 0
