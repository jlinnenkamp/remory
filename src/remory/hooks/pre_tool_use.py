"""Claude Code PreToolUse hook — refuse Edit/Write to state.md.

Implements consolidated plan §8.2. The hook intercepts every Edit/Write
tool invocation; if the resolved target is exactly
``<data_dir>/topics/<name>/state.md`` for some direct topic child, the
hook denies the operation. The user sees the §5.8 refusal message in the
claude session.

Symlinks are resolved before matching (``Path.resolve(strict=False)``).
Basename-only matching is rejected by design — a file named ``state.md``
outside any topic dir is allowed; this is pinned by a unit test.

The hook protocol assumed here (snapshot-pinned by
``tests/unit/test_hook_pre_tool_use.py``):

- ``stdin`` is a JSON object with keys ``tool_name`` and ``tool_input``.
- ``tool_input`` is an object; we read ``file_path`` (the path the tool
  would write/edit). Missing means we have no path to match → allow.
- On allow: write ``{"continue": true}`` to stdout, exit 0.
- On deny: write ``{"continue": false, "stopReason": <message>}`` to
  stdout, exit 2 (claude's "block" exit code).
"""

from __future__ import annotations

import io
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from remory import config as cfgmod
from remory import paths

__all__ = [
    "PRE_TOOL_USE_REFUSAL_MESSAGE",
    "PreToolUseDecision",
    "PreToolUseInput",
    "decide",
    "main",
]

_log = logging.getLogger("remory.hooks.pre_tool_use")


# §5.8 verbatim. Trailing newline is part of the contract.
PRE_TOOL_USE_REFUSAL_MESSAGE: Final[str] = (
    "state.md is updated only during `remory sleep`. Refusing the write.\n"
)


_BLOCKED_TOOLS: Final[frozenset[str]] = frozenset({"Edit", "Write"})


# ---------------------------------------------------------------------------
# IO models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreToolUseInput:
    """Parsed input from the claude hook payload."""

    tool_name: str
    target_path: Path | None


@dataclass(frozen=True)
class PreToolUseDecision:
    """Allow / deny outcome. ``message`` is empty on allow."""

    allowed: bool
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_data_dir() -> Path:
    try:
        cfg = cfgmod.load_config()
    except cfgmod.ConfigError:
        return paths.data_dir()
    return cfgmod.resolve_data_dir(cfg)


def _is_topic_state_md(target: Path, data_dir: Path) -> bool:
    """True iff ``target`` resolves to ``<data_dir>/topics/<name>/state.md``.

    Symlinks are resolved before matching. The parent dir must be a
    direct child of ``<data_dir>/topics/``; nested paths are rejected.
    Basename-only matching is rejected — a ``state.md`` outside the
    topics tree returns False.
    """
    try:
        resolved = target.resolve(strict=False)
    except OSError:
        return False
    if resolved.name != "state.md":
        return False
    topics_root = (data_dir / "topics").resolve()
    parent = resolved.parent
    return parent.parent == topics_root


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


def decide(payload: PreToolUseInput) -> PreToolUseDecision:
    """Allow iff the operation does not target a topic's ``state.md``.

    Allow cases:

    - ``tool_name`` not in {Edit, Write}.
    - ``target_path`` is None (no path to match).
    - Resolved target is NOT a direct topic-child ``state.md``.

    Deny case: resolved target matches the topic ``state.md`` pattern.
    The deny message is :data:`PRE_TOOL_USE_REFUSAL_MESSAGE` (§5.8
    verbatim).
    """
    if payload.tool_name not in _BLOCKED_TOOLS:
        return PreToolUseDecision(allowed=True, message="")
    if payload.target_path is None:
        return PreToolUseDecision(allowed=True, message="")
    data_dir = _resolve_data_dir()
    if not _is_topic_state_md(payload.target_path, data_dir):
        return PreToolUseDecision(allowed=True, message="")
    return PreToolUseDecision(allowed=False, message=PRE_TOOL_USE_REFUSAL_MESSAGE)


# ---------------------------------------------------------------------------
# CLI shim
# ---------------------------------------------------------------------------


def _parse_stdin(stdin: io.TextIOBase | None) -> dict[str, object]:
    """Read the claude PreToolUse payload from stdin.

    Returns an empty dict on any parse failure; the shim then builds an
    "allow" decision (tool_name == "" not in BLOCKED).
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


def _coerce_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _build_input(payload: dict[str, object]) -> PreToolUseInput:
    """Build :class:`PreToolUseInput` from a parsed payload.

    Pinned keys:

    - ``tool_name`` — the claude tool name (e.g. ``"Edit"``, ``"Write"``).
    - ``tool_input.file_path`` — the path the tool would touch. Also
      accepts ``tool_input.path`` (defensive against shape variants).
    - We also accept the flat ``target_path`` / ``file_path`` keys as
      fallbacks for test fixtures and future hook-protocol shifts.
    """
    tool_name = _coerce_str(payload.get("tool_name"))
    tool_input = payload.get("tool_input")
    file_path_str = ""
    if isinstance(tool_input, dict):
        ti = cast("dict[str, object]", tool_input)
        file_path_str = _coerce_str(ti.get("file_path")) or _coerce_str(ti.get("path"))
    if not file_path_str:
        file_path_str = _coerce_str(payload.get("file_path")) or _coerce_str(
            payload.get("target_path")
        )
    target_path = Path(file_path_str) if file_path_str else None
    return PreToolUseInput(tool_name=tool_name, target_path=target_path)


def main(argv: list[str] | None = None, stdin: io.TextIOBase | None = None) -> int:
    """Thin shim invoked from the ``remory _hook pretool`` Typer subapp.

    Reads the claude PreToolUse payload from stdin, calls :func:`decide`,
    writes the allow/deny JSON response to stdout, and returns claude's
    exit code: 0 on allow, 2 on deny (block).
    """
    del argv
    payload = _parse_stdin(stdin)
    inp = _build_input(payload)
    decision = decide(inp)
    if decision.allowed:
        sys.stdout.write(json.dumps({"continue": True}) + "\n")
        return 0
    # Deny: surface §5.8 message via claude's stopReason. The newline at
    # the end of the refusal is part of the §5.8 contract.
    sys.stdout.write(json.dumps({"continue": False, "stopReason": decision.message}) + "\n")
    sys.stderr.write(decision.message)
    return 2
