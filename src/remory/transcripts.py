"""Claude Code transcript reader and markdown renderer.

This module is **wire format**. Phase 4 chat and Phase 6 SessionEnd hook
both call :func:`to_markdown`; raw entries persist on disk indefinitely as
part of users' personal data. Changing the renderer's output is a change
to user data shape and requires a forward-compat plan, like
:class:`remory.raw.RawStatus`.

Path encoding is a contract with the real ``claude`` CLI. The CLI writes
session JSONLs into ``~/.claude/projects/<encoded-cwd>/<session_id>.jsonl``
where the encoding replaces every ``/`` and every ``.`` in the absolute
cwd path with ``-``. :func:`encode_cwd_for_claude` replicates that
encoding; the gated real-CLI integration test enforces parity.

Example output of :func:`to_markdown` for a two-event canonical
transcript::

    **You:** What's the deal with airline food?

    **Remory:** It is a useful prompt for thinking about constraints under load.

Renderer pins (enforced by tests):

1. Role labels are exactly ``**You:** `` and ``**Remory:** ``.
2. Turn ordering follows JSONL line order.
3. Multi-block assistant content joined with ``\\n\\n``;
   ``tool_use`` blocks render as ``<!-- tool: <name> -->`` placeholders;
   unknown block types silently dropped.
4. Sidechain / system / summary / queue events dropped silently.
5. Empty (``.strip()``-empty) text blocks skipped; turns with no surviving
   blocks skipped entirely.
6. No escaping of markdown special characters in user/assistant text.
7. No code-fence wrapping.
8. UTF-8 in, UTF-8 out, no BOM.
9. Trailing newline: exactly one ``\\n``.
10. Inter-turn separation: two ``\\n`` characters (one blank line).
11. Empty transcript (no user/assistant events) returns ``""``.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TranscriptEvent",
    "TranscriptParseError",
    "claude_projects_dir",
    "encode_cwd_for_claude",
    "iter_events",
    "locate_latest",
    "project_dir_for",
    "to_markdown",
]


_log = logging.getLogger("remory.transcripts")


# ---------------------------------------------------------------------------
# Models / errors
# ---------------------------------------------------------------------------


class TranscriptEvent(BaseModel):
    """One JSONL event from a Claude Code transcript.

    Permissive on extra fields (``extra="ignore"``) because Claude Code's
    transcript shape varies across CLI versions and we read fields we
    care about, ignoring the rest.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    type: str
    message: dict[str, Any] | None = None
    uuid: str | None = None
    timestamp: str | None = None
    session_id: str | None = Field(default=None, alias="sessionId")
    is_sidechain: bool = Field(default=False, alias="isSidechain")


class TranscriptParseError(Exception):
    """Raised when a transcript file cannot be opened or read."""


# ---------------------------------------------------------------------------
# Path encoding
# ---------------------------------------------------------------------------


def encode_cwd_for_claude(cwd: Path) -> str:
    """Encode an absolute cwd into Claude Code's projects-dir basename.

    Algorithm: take the absolute path string, replace each ``/`` with
    ``-``, replace each ``.`` with ``-``. Returns the encoded basename
    used as ``~/.claude/projects/<encoded>``.
    """
    s = str(cwd)
    return s.replace("/", "-").replace(".", "-")


def claude_projects_dir() -> Path:
    """Return the directory where Claude Code stores per-project transcripts.

    Honors ``$FAKE_CLAUDE_HOME`` so tests (and the fake binary) can agree
    on a writable temp location: when ``FAKE_CLAUDE_HOME`` is set, returns
    ``$FAKE_CLAUDE_HOME/projects``; otherwise ``~/.claude/projects``.
    """
    fake_home = os.environ.get("FAKE_CLAUDE_HOME")
    if fake_home:
        return Path(fake_home) / "projects"
    return Path.home() / ".claude" / "projects"


def project_dir_for(cwd: Path) -> Path:
    """Return the projects subdirectory for ``cwd`` (resolved absolute)."""
    return claude_projects_dir() / encode_cwd_for_claude(cwd.resolve())


def locate_latest(cwd: Path) -> Path | None:
    """Return the newest ``*.jsonl`` (by mtime) in ``project_dir_for(cwd)``.

    Returns ``None`` if the project directory does not exist or contains
    no ``*.jsonl`` files.
    """
    pdir = project_dir_for(cwd)
    if not pdir.is_dir():
        return None
    candidates = list(pdir.glob("*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------------------
# Event iteration
# ---------------------------------------------------------------------------


def iter_events(path: Path) -> Iterator[TranscriptEvent]:
    """Yield :class:`TranscriptEvent` instances from a JSONL transcript file.

    Lines that fail to parse as JSON or fail Pydantic validation log a
    WARNING and are skipped. File-open errors raise
    :class:`TranscriptParseError`.
    """
    try:
        f = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise TranscriptParseError(f"could not open transcript {path}: {exc}") from exc

    with f:
        for lineno, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                _log.warning(
                    "transcript %s: skipping malformed JSON on line %d: %s",
                    path,
                    lineno,
                    exc,
                )
                continue
            if not isinstance(data, dict):
                _log.warning(
                    "transcript %s: skipping non-object JSON on line %d",
                    path,
                    lineno,
                )
                continue
            try:
                event = TranscriptEvent.model_validate(data)
            except Exception as exc:  # pragma: no cover - permissive ignore should rarely fail
                _log.warning(
                    "transcript %s: skipping unvalidatable event on line %d: %s",
                    path,
                    lineno,
                    exc,
                )
                continue
            yield event


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


_USER_LABEL = "**You:** "
_ASSISTANT_LABEL = "**Remory:** "


def _render_blocks(blocks: list[Any]) -> str:
    """Join content blocks for a single turn per pin 3 / pin 5."""
    rendered: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_dict: dict[str, Any] = cast("dict[str, Any]", block)
        btype = block_dict.get("type")
        if btype == "text":
            text = block_dict.get("text", "")
            if not isinstance(text, str):
                continue
            if text.strip() == "":
                continue
            rendered.append(text)
        elif btype == "tool_use":
            name = block_dict.get("name", "?")
            rendered.append(f"<!-- tool: {name} -->")
        # other block types: silently dropped
    return "\n\n".join(rendered)


def _render_user_content(content: Any) -> str:
    """Render a user message's ``content`` field.

    Real CLI varies: ``content`` may be a string OR a list of blocks. We
    handle both. Strings are treated as a single text block.
    """
    if isinstance(content, str):
        if content.strip() == "":
            return ""
        return content
    if isinstance(content, list):
        return _render_blocks(cast("list[Any]", content))
    return ""


def to_markdown(path: Path) -> str:
    """Render a transcript file as markdown.

    See module docstring for the 11 renderer pins; tests enforce them.
    """
    turns: list[str] = []
    last_timestamp: str | None = None

    for event in iter_events(path):
        if event.is_sidechain:
            continue
        etype = event.type
        if etype not in ("user", "assistant"):
            # system / summary / queue / anything else
            continue

        # Out-of-order timestamps: WARN but do not raise (pin 2).
        ts = event.timestamp
        if ts is not None and last_timestamp is not None and ts < last_timestamp:
            _log.warning(
                "transcript %s: event timestamp %s precedes previous %s; "
                "rendering in JSONL line order",
                path,
                ts,
                last_timestamp,
            )
        if ts is not None:
            last_timestamp = ts

        message = event.message
        if message is None:
            continue
        content = message.get("content")

        if etype == "user":
            body = _render_user_content(content)
            if body == "":
                continue
            turns.append(f"{_USER_LABEL}{body}")
        else:  # assistant
            if not isinstance(content, list):
                continue
            body = _render_blocks(cast("list[Any]", content))
            if body == "":
                continue
            turns.append(f"{_ASSISTANT_LABEL}{body}")

    if not turns:
        return ""

    return "\n\n".join(turns) + "\n"
