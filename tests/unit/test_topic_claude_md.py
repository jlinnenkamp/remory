"""Tests for :mod:`remory.topic_claude_md`.

Cover:

- Byte-stable ``render`` for each built-in schema x representative knobs.
- Stamp inclusion.
- Tone/strictness dispatch table coverage (all three values per axis).
- ``regenerate_if_stale`` per the plan §6.5 contract: write-when-missing,
  rewrite-when-stamp-older, rewrite-when-knobs-changed, skip-on-byte-match.
- The lock is acquired with ``timeout=0.0`` (multiprocess contention raises).

See plan §11.1.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from remory.claude_assets import PRODUCTION_TEMPLATE_VERSION, detect_version
from remory.locking import LockBusyError, topic_lock
from remory.schema import load_builtin
from remory.topic import Knobs, Topic, TopicMeta, write_meta
from remory.topic_claude_md import (
    EmitEntry,
    TopicClaudeMdContext,
    regenerate_if_stale,
    render,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _build_ctx(
    schema_name: str,
    tone: Literal["warm", "balanced", "direct"],
    strictness: Literal["gentle", "balanced", "rigorous"],
) -> TopicClaudeMdContext:
    schema = load_builtin(schema_name)
    return TopicClaudeMdContext(
        schema_name=schema.name,
        persona=schema.persona,
        tone=tone,
        strictness=strictness,
    )


def _expected_text(
    schema_name: str,
    persona: str,
    tone_line: str,
    strictness_line: str,
) -> str:
    """Build the expected `render` output from first principles.

    Pinned to plan §5.7. If this function ever drifts from the
    implementation, the byte-stable tests fail — that's the contract.
    """
    return (
        f"<!-- remory: template_version={PRODUCTION_TEMPLATE_VERSION} -->\n"
        f"# Topic: {schema_name}\n"
        "\n"
        f'You are the assistant for the user\'s "{schema_name}" topic in Remory.\n'
        "Read `state.md` at the start of each session — it is your canonical\n"
        "context for what is already known about this topic. Treat `state.md`\n"
        "as read-only. You will be blocked at the tool level from editing it\n"
        "during this chat (sleep is the only writer).\n"
        "\n"
        "## Persona for this topic\n"
        "\n"
        f"{persona}\n"
        "\n"
        "## How the user wants to be spoken to\n"
        "\n"
        f"{tone_line}\n"
        f"{strictness_line}\n"
        "\n"
        "## Practical rules\n"
        "\n"
        "- Do not edit `state.md`. It is updated only during sleep.\n"
        "- Do not write new files outside the topic directory.\n"
        "- If something the user says contradicts `state.md`, surface the\n"
        "  contradiction; do not silently overwrite the older view.\n"
        "- Slash commands available in this session: `/sleep`, `/state`,\n"
        "  `/recent`, `/review`.\n"
        "\n"
        "## Pointer\n"
        "\n"
        "The canonical context for this topic is in `state.md`. The user's\n"
        "broader self-description (name, the wish they brought to Remory) is\n"
        "in `../../about-me.md`.\n"
    )


# Tone/strictness literal lines (kept in lock-step with §5.7 dispatch tables).
_TONE_LINE_WARM = "Warm. Meet the user where they are; flag contradictions kindly."
_TONE_LINE_BALANCED = "Balanced. Acknowledge feelings, but be useful first."
_TONE_LINE_DIRECT = "Direct. Skip the warm-up; say what you see."

_STRICT_LINE_GENTLE = (
    "Gentle. Take the user's claims as offered unless evidence in state.md says otherwise."
)
_STRICT_LINE_BALANCED = "Balanced. Test claims lightly when they conflict with state.md."
_STRICT_LINE_RIGOROUS = (
    "Rigorous. Stress-test claims; ask for evidence before accepting big changes."
)


# ---------------------------------------------------------------------------
# Byte-stable snapshot tests (one per built-in schema)
# ---------------------------------------------------------------------------


def test_render_byte_stable_for_workout_warm_balanced() -> None:
    schema = load_builtin("workout")
    out = render(_build_ctx("workout", "warm", "balanced"))
    assert out == _expected_text("workout", schema.persona, _TONE_LINE_WARM, _STRICT_LINE_BALANCED)


def test_render_byte_stable_for_coaching_warm_gentle() -> None:
    schema = load_builtin("coaching")
    out = render(_build_ctx("coaching", "warm", "gentle"))
    assert out == _expected_text("coaching", schema.persona, _TONE_LINE_WARM, _STRICT_LINE_GENTLE)


def test_render_byte_stable_for_job_profile_warm_balanced() -> None:
    schema = load_builtin("job-profile")
    out = render(_build_ctx("job-profile", "warm", "balanced"))
    assert out == _expected_text(
        "job-profile", schema.persona, _TONE_LINE_WARM, _STRICT_LINE_BALANCED
    )


def test_render_includes_template_version_stamp() -> None:
    out = render(_build_ctx("workout", "warm", "balanced"))
    assert out.startswith(f"<!-- remory: template_version={PRODUCTION_TEMPLATE_VERSION} -->\n")
    assert detect_version(out) == PRODUCTION_TEMPLATE_VERSION


def test_render_tone_line_dispatch_table_covers_all_three_values() -> None:
    cases: tuple[tuple[Literal["warm", "balanced", "direct"], str], ...] = (
        ("warm", _TONE_LINE_WARM),
        ("balanced", _TONE_LINE_BALANCED),
        ("direct", _TONE_LINE_DIRECT),
    )
    for tone, expected_line in cases:
        out = render(_build_ctx("workout", tone, "balanced"))
        assert expected_line in out


def test_render_strictness_line_dispatch_table_covers_all_three_values() -> None:
    cases: tuple[tuple[Literal["gentle", "balanced", "rigorous"], str], ...] = (
        ("gentle", _STRICT_LINE_GENTLE),
        ("balanced", _STRICT_LINE_BALANCED),
        ("rigorous", _STRICT_LINE_RIGOROUS),
    )
    for strictness, expected_line in cases:
        out = render(_build_ctx("workout", "warm", strictness))
        assert expected_line in out


# ---------------------------------------------------------------------------
# regenerate_if_stale tests
# ---------------------------------------------------------------------------


def _make_topic(
    base: Path,
    schema_name: str,
    *,
    tone: Literal["warm", "balanced", "direct"] = "warm",
    strictness: Literal["gentle", "balanced", "rigorous"] = "balanced",
) -> tuple[Path, Topic]:
    """Build a topic dir under ``<base>/topics/<schema_name>``.

    The directory must be two levels under ``base`` so the
    ``topic_dir.parent.parent`` derivation of ``data_dir`` inside
    ``regenerate_if_stale`` lands on ``base``.
    """
    schema = load_builtin(schema_name)
    topic_dir = base / "topics" / schema_name
    topic_dir.mkdir(parents=True, exist_ok=True)
    knobs = Knobs(tone=tone, strictness=strictness)
    meta = TopicMeta(
        schema=schema_name,
        schema_version=schema.version,
        created=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        pending_count=0,
        total_entries=0,
        knobs=knobs,
    )
    with topic_lock(topic_dir):
        write_meta(topic_dir, meta)
    from remory.topic import load_topic

    return topic_dir, load_topic(topic_dir)


def test_regenerate_if_stale_writes_when_file_missing(tmp_path: Path) -> None:
    topic_dir, topic = _make_topic(tmp_path, "workout")
    target = topic_dir / "CLAUDE.md"
    assert not target.exists()
    entry = regenerate_if_stale(topic_dir, topic=topic, force=False, dry_run=False)
    assert isinstance(entry, EmitEntry)
    assert entry.action == "write"
    assert target.is_file()
    assert detect_version(target.read_text(encoding="utf-8")) == PRODUCTION_TEMPLATE_VERSION


def test_regenerate_if_stale_writes_when_stamp_older(tmp_path: Path) -> None:
    topic_dir, topic = _make_topic(tmp_path, "workout")
    target = topic_dir / "CLAUDE.md"
    # Seed an older-stamped CLAUDE.md.
    target.write_bytes(b"<!-- remory: template_version=0 -->\nold stuff\n")
    entry = regenerate_if_stale(topic_dir, topic=topic, force=False, dry_run=False)
    assert isinstance(entry, EmitEntry)
    assert entry.action == "overwrite"
    assert entry.reason == "stamp-older"
    # The new bytes match a freshly-rendered context.
    expected = render(
        TopicClaudeMdContext(
            schema_name=topic.schema.name,
            persona=topic.schema.persona,
            tone=topic.meta.knobs.tone,
            strictness=topic.meta.knobs.strictness,
        )
    ).encode("utf-8")
    assert target.read_bytes() == expected
    # A backup was written at the data-dir-level backups dir.
    backups_dir = tmp_path / ".claude" / ".backups"
    baks = list(backups_dir.glob("topics__workout__CLAUDE.md.*.bak"))
    assert len(baks) == 1


def test_regenerate_if_stale_writes_when_knobs_changed_in_meta(tmp_path: Path) -> None:
    # First render with one set of knobs, then change knobs and re-render.
    topic_dir, topic = _make_topic(tmp_path, "workout", tone="warm", strictness="balanced")
    target = topic_dir / "CLAUDE.md"
    first = regenerate_if_stale(topic_dir, topic=topic, force=False, dry_run=False)
    assert first is not None
    assert first.action == "write"

    # Change knobs on disk: rewrite meta with new tone, then re-load topic.
    new_meta = topic.meta.model_copy(update={"knobs": Knobs(tone="direct", strictness="rigorous")})
    with topic_lock(topic_dir):
        write_meta(topic_dir, new_meta)
    from remory.topic import load_topic

    new_topic = load_topic(topic_dir)

    # The on-disk file is current-stamp + bytes-different => conflict;
    # but with force=True we should overwrite.
    second = regenerate_if_stale(topic_dir, topic=new_topic, force=True, dry_run=False)
    assert second is not None
    assert second.action == "overwrite"
    assert _STRICT_LINE_RIGOROUS in target.read_text(encoding="utf-8")
    assert _TONE_LINE_DIRECT in target.read_text(encoding="utf-8")


def test_regenerate_if_stale_skips_when_byte_identical(tmp_path: Path) -> None:
    topic_dir, topic = _make_topic(tmp_path, "workout")
    # First-time write.
    regenerate_if_stale(topic_dir, topic=topic, force=False, dry_run=False)
    # Second call — nothing should change.
    entry = regenerate_if_stale(topic_dir, topic=topic, force=False, dry_run=False)
    assert isinstance(entry, EmitEntry)
    assert entry.action == "skip"
    assert entry.reason == "unchanged"


def test_regenerate_if_stale_acquires_topic_lock_timeout_zero(
    tmp_path: Path,
    multi_process_lock_holder: Callable[[Path], subprocess.Popen[str]],
) -> None:
    """If another process already holds the lock, ``regenerate_if_stale``
    fails fast (timeout=0.0 surfaces as :class:`LockBusyError`)."""
    topic_dir, topic = _make_topic(tmp_path, "workout")
    proc = multi_process_lock_holder(topic_dir)
    try:
        # The child has signalled LOCKED; the in-process attempt must
        # raise without blocking.
        t0 = time.monotonic()
        with pytest.raises(LockBusyError):
            regenerate_if_stale(topic_dir, topic=topic, force=False, dry_run=False)
        elapsed = time.monotonic() - t0
        # Should be near-instantaneous — well under the 50ms poll
        # interval used by the timed branch (which we're NOT exercising).
        assert elapsed < 1.0
    finally:
        if proc.stdin is not None:
            proc.stdin.close()


# ---------------------------------------------------------------------------
# Combinator: regen_all_topic_claude_md
# ---------------------------------------------------------------------------


def test_regen_all_topic_claude_md_continues_when_one_topic_has_malformed_meta(
    tmp_path: Path,
) -> None:
    """The malformed-meta topic emits a skip entry; other topics still
    get regenerated. Pins plan §6.5 'does NOT abort the whole refresh'."""
    from remory.topic_claude_md import regen_all_topic_claude_md

    # Seed two topics — one valid, one with broken meta.yaml.
    good_dir, _good_topic = _make_topic(tmp_path, "workout")
    bad_dir = tmp_path / "topics" / "broken"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "meta.yaml").write_bytes(b"not: [valid yaml: at all\n")

    entries = regen_all_topic_claude_md(tmp_path, force=False, dry_run=False)
    # We should have entries for both: 'broken' is meta-malformed, 'workout' is write.
    by_path = {e.path: e for e in entries}
    bad_entry = by_path[bad_dir / "CLAUDE.md"]
    assert bad_entry.action == "skip"
    assert bad_entry.reason == "meta-malformed"
    good_entry = by_path[good_dir / "CLAUDE.md"]
    assert good_entry.action == "write"
    assert (good_dir / "CLAUDE.md").is_file()


def test_regen_all_topic_claude_md_silently_skips_dirs_without_meta_yaml(
    tmp_path: Path,
) -> None:
    """A directory under topics/ that has no meta.yaml at all is not a
    real topic — no entry is emitted (silent skip)."""
    from remory.topic_claude_md import regen_all_topic_claude_md

    # A bare directory under topics/ with no meta.yaml.
    bare = tmp_path / "topics" / "stray"
    bare.mkdir(parents=True, exist_ok=True)
    entries = regen_all_topic_claude_md(tmp_path, force=False, dry_run=False)
    assert entries == ()
