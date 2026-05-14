"""Per-topic ``CLAUDE.md`` generator and refresher.

Owns the deterministic ``CLAUDE.md`` template per consolidated plan §5.7
and the regeneration policy from §6.5: re-render every topic, write when
missing, stamp-older, knobs changed, or bytes drift; conflict-handle
identically to :func:`remory.claude_assets.install_data_dir_templates`.

The ``EmitEntry`` model is the per-topic counterpart of
:class:`remory.claude_assets.SkippedEntry`. The combined-refresh path in
:func:`remory.claude_assets.refresh` adopts these entries into one
:class:`remory.claude_assets.EmitResult`.

Backups for per-topic ``CLAUDE.md`` writes go under the data-dir-level
``<data_dir>/.claude/.backups/`` (the wizard owns this backup space),
not under each topic's own ``.backups/``. See plan §4.4.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from remory import paths
from remory.atomic import atomic_write_bytes
from remory.claude_assets import (
    PRODUCTION_TEMPLATE_VERSION,
    detect_version,
    emit_backup,
)
from remory.locking import topic_lock
from remory.topic import Topic, TopicMetaError, load_topic

__all__ = [
    "EmitEntry",
    "TopicClaudeMdContext",
    "regen_all_topic_claude_md",
    "regenerate_if_stale",
    "render",
]

_log = logging.getLogger("remory.topic_claude_md")


# ---------------------------------------------------------------------------
# §5.7 template literal + dispatch tables
# ---------------------------------------------------------------------------

# The literal body, with placeholders. End with a single trailing newline.
# Stamp is prepended by `render` via the same convention as bundled templates.
_TOPIC_CLAUDE_MD_BODY: Final[str] = """\
# Topic: {schema_name}

You are the assistant for the user's "{schema_name}" topic in Remory.
Read `state.md` at the start of each session — it is your canonical
context for what is already known about this topic. Treat `state.md`
as read-only. You will be blocked at the tool level from editing it
during this chat (sleep is the only writer).

## Persona for this topic

{persona}

## How the user wants to be spoken to

{tone_line}
{strictness_line}

## Practical rules

- Do not edit `state.md`. It is updated only during sleep.
- Do not write new files outside the topic directory.
- If something the user says contradicts `state.md`, surface the
  contradiction; do not silently overwrite the older view.
- Slash commands available in this session: `/sleep`, `/state`,
  `/recent`, `/review`.

## Pointer

The canonical context for this topic is in `state.md`. The user's
broader self-description (name, the wish they brought to Remory) is
in `../../about-me.md`.
"""


_TONE_LINES: Final[dict[str, str]] = {
    "warm": "Warm. Meet the user where they are; flag contradictions kindly.",
    "balanced": "Balanced. Acknowledge feelings, but be useful first.",
    "direct": "Direct. Skip the warm-up; say what you see.",
}

_STRICTNESS_LINES: Final[dict[str, str]] = {
    "gentle": (
        "Gentle. Take the user's claims as offered unless evidence in state.md says otherwise."
    ),
    "balanced": "Balanced. Test claims lightly when they conflict with state.md.",
    "rigorous": ("Rigorous. Stress-test claims; ask for evidence before accepting big changes."),
}


# ---------------------------------------------------------------------------
# Public dataclass + Pydantic models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopicClaudeMdContext:
    """Render-time inputs for the per-topic ``CLAUDE.md``."""

    schema_name: str
    persona: str
    tone: Literal["warm", "balanced", "direct"]
    strictness: Literal["gentle", "balanced", "rigorous"]


class EmitEntry(BaseModel):
    """One topic's outcome from a refresh pass.

    The ``reason`` codes mirror those on
    :class:`remory.claude_assets.SkippedEntry` plus the per-topic
    ``meta-malformed`` and ``knobs-changed`` codes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    action: Literal["write", "overwrite", "skip"]
    reason: str
    current_version: int | None = None
    on_disk_version: int | None = None


# ---------------------------------------------------------------------------
# Pure render
# ---------------------------------------------------------------------------


def render(ctx: TopicClaudeMdContext) -> str:
    """Render the per-topic ``CLAUDE.md`` bytes (UTF-8 text, in fact).

    Byte-stable for fixed inputs (snapshot-tested per ``(schema, tone,
    strictness)`` tuple). Stamped with
    :data:`remory.claude_assets.PRODUCTION_TEMPLATE_VERSION`.
    """
    tone_line = _TONE_LINES[ctx.tone]
    strictness_line = _STRICTNESS_LINES[ctx.strictness]
    body = _TOPIC_CLAUDE_MD_BODY.format(
        schema_name=ctx.schema_name,
        persona=ctx.persona,
        tone_line=tone_line,
        strictness_line=strictness_line,
    )
    stamp = f"<!-- remory: template_version={PRODUCTION_TEMPLATE_VERSION} -->\n"
    return stamp + body


# ---------------------------------------------------------------------------
# regenerate_if_stale
# ---------------------------------------------------------------------------


def _classify_target(
    target: Path,
    rendered: bytes,
    *,
    force: bool,
) -> tuple[str, str, int | None]:
    """Decide what to do for a single topic CLAUDE.md.

    Returns ``(action, reason, on_disk_version)``. ``action`` is one of
    ``"write"`` (file missing), ``"overwrite"`` (replace + .bak), or
    ``"skip"`` (no-op).
    """
    if not target.exists():
        return "write", "missing", None

    on_disk = target.read_bytes()
    if on_disk == rendered:
        on_disk_version = detect_version(on_disk.decode("utf-8", errors="replace"))
        return "skip", "unchanged", on_disk_version

    try:
        on_disk_text = on_disk.decode("utf-8")
    except UnicodeDecodeError:
        on_disk_text = ""
    on_disk_version = detect_version(on_disk_text)

    if on_disk_version is None:
        # No stamp on disk — preserve (D5). --force does NOT override.
        return "skip", "unstamped-preserved", None

    if on_disk_version > PRODUCTION_TEMPLATE_VERSION:
        # Newer-on-disk — warn but skip (ADR-0005).
        _log.warning(
            "topic CLAUDE.md has a newer template version than this remory; skipping",
            extra={
                "path": str(target),
                "on_disk_version": on_disk_version,
                "current_version": PRODUCTION_TEMPLATE_VERSION,
            },
        )
        return "skip", "newer-on-disk", on_disk_version

    if on_disk_version < PRODUCTION_TEMPLATE_VERSION:
        return "overwrite", "stamp-older", on_disk_version

    # Same version, bytes differ — knobs/persona changed OR user edited.
    # We treat byte-drift on a current-stamp file as conflict, same as
    # install_data_dir_templates. --force overrides.
    if force:
        return "overwrite", "stamped-but-edited", on_disk_version
    return "skip", "stamped-but-edited", on_disk_version


def regenerate_if_stale(
    topic_dir: Path,
    *,
    topic: Topic,
    force: bool = False,
    dry_run: bool = False,
) -> EmitEntry | None:
    """Re-render the topic's ``CLAUDE.md`` and write iff stale.

    Acquires :func:`remory.locking.topic_lock` with ``timeout=0.0``; the
    caller must not already hold it. Conflict handling mirrors
    :func:`remory.claude_assets.install_data_dir_templates`: refuse on
    stamped-but-edited unless ``force``; ``.bak`` on every overwrite.
    ``--force`` does NOT overwrite unstamped files (D5).

    Returns an :class:`EmitEntry` describing the action, or ``None`` if
    the file was already byte-identical (caller can treat ``None`` as
    "fully unchanged"). Note: the combined-refresh caller in
    :func:`remory.claude_assets.refresh` does NOT treat ``None`` as
    different from a ``skip`` entry — it just records whatever it gets.
    Today this function always returns a non-None entry; the ``| None``
    in the return type is reserved for a future "compute hash without
    reading bytes" optimisation.
    """
    ctx = TopicClaudeMdContext(
        schema_name=topic.schema.name,
        persona=topic.schema.persona,
        tone=topic.meta.knobs.tone,
        strictness=topic.meta.knobs.strictness,
    )
    rendered_text = render(ctx)
    rendered = rendered_text.encode("utf-8")
    target = paths.claude_md_file(topic_dir)

    with topic_lock(topic_dir, timeout=0.0):
        action, reason, on_disk_version = _classify_target(target, rendered, force=force)

        if action == "write":
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, rendered)
            return EmitEntry(
                path=target,
                action="write",
                reason=reason,
                current_version=PRODUCTION_TEMPLATE_VERSION,
                on_disk_version=on_disk_version,
            )

        if action == "overwrite":
            data_dir = topic_dir.parent.parent  # <data_dir>/topics/<name>
            if not dry_run:
                emit_backup(target, data_dir)
                atomic_write_bytes(target, rendered)
            return EmitEntry(
                path=target,
                action="overwrite",
                reason=reason,
                current_version=PRODUCTION_TEMPLATE_VERSION,
                on_disk_version=on_disk_version,
            )

        return EmitEntry(
            path=target,
            action="skip",
            reason=reason,
            current_version=PRODUCTION_TEMPLATE_VERSION,
            on_disk_version=on_disk_version,
        )


# ---------------------------------------------------------------------------
# regen_all_topic_claude_md
# ---------------------------------------------------------------------------


def regen_all_topic_claude_md(
    data_dir: Path,
    *,
    force: bool,
    dry_run: bool,
) -> tuple[EmitEntry, ...]:
    """Iterate every topic under ``<data_dir>/topics/`` and regenerate.

    Topics without a ``meta.yaml`` are skipped silently (they aren't
    real topics). Topics with a malformed ``meta.yaml`` emit a single
    :class:`EmitEntry` with ``action="skip"`` and
    ``reason="meta-malformed"`` and the iteration continues — a bad
    meta does NOT abort the whole refresh.
    """
    topics_dir = data_dir / "topics"
    if not topics_dir.is_dir():
        return ()

    entries: list[EmitEntry] = []
    for child in sorted(topics_dir.iterdir()):
        if not child.is_dir():
            continue
        meta_path = paths.meta_file(child)
        if not meta_path.is_file():
            # Not a real topic — silently skip (no entry).
            continue
        try:
            topic = load_topic(child)
        except TopicMetaError as exc:
            _log.warning(
                "skipping topic with malformed meta.yaml",
                extra={
                    "topic_dir": str(child),
                    "exception_type": type(exc).__name__,
                },
            )
            entries.append(
                EmitEntry(
                    path=paths.claude_md_file(child),
                    action="skip",
                    reason="meta-malformed",
                    current_version=PRODUCTION_TEMPLATE_VERSION,
                    on_disk_version=None,
                )
            )
            continue
        entry = regenerate_if_stale(child, topic=topic, force=force, dry_run=dry_run)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)
