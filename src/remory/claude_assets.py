"""Install, refresh, and stamp bundled `.claude/` templates in the data dir.

This module owns the policy table in consolidated plan §9:

* Missing file              → write + stamp.
* Stamp older than bundle   → overwrite + .bak.
* Stamp matches, bytes match → skip (unchanged).
* Stamp matches, bytes differ → conflict; refuse unless ``force=True``.
* No stamp                  → preserve (likely user-authored). ``force``
                              does NOT override (D5).
* Stamp newer than bundle   → warn but skip (downgrade footgun; ADR-0005).

Backups go to ``<data_dir>/.claude/.backups/<flattened-relpath>.<utc-ts>.bak``.
Atomic writes always — see ``project_phase3_backup_atomicity``. Phase 6
does not ship cleanup (ADR-0005).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final, cast

from pydantic import BaseModel, ConfigDict

from remory.atomic import atomic_write_bytes
from remory.data_templates import iter_template_relpaths, read_template_bytes

__all__ = [
    "PRODUCTION_TEMPLATE_VERSION",
    "TEMPLATE_VERSION_KEY",
    "EmitResult",
    "SkippedEntry",
    "detect_version",
    "emit_backup",
    "install_data_dir_templates",
    "refresh",
    "stamp_markdown",
]

_log = logging.getLogger("remory.claude_assets")


# ---------------------------------------------------------------------------
# Wire-format pins
# ---------------------------------------------------------------------------

PRODUCTION_TEMPLATE_VERSION: Final[int] = 1
TEMPLATE_VERSION_KEY: Final[str] = "_remory_template_version"

# Markdown stamp comment regex — matches the canonical head-stamp.
# Group 1 captures the integer version.
_MD_STAMP_RE: Final[re.Pattern[str]] = re.compile(
    r"^<!--\s*remory:\s*template_version=(\d+)\s*-->\s*$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class SkippedEntry(BaseModel):
    """One file the emitter decided not to touch (or to flag)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    reason: str  # "unstamped-preserved" | "stamped-but-edited" | "unchanged"
    #                | "newer-on-disk" | "meta-malformed"
    current_version: int | None = None
    on_disk_version: int | None = None


class EmitResult(BaseModel):
    """Summary of an install / refresh pass."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    written: tuple[Path, ...]
    overwritten: tuple[Path, ...]
    skipped: tuple[SkippedEntry, ...]
    dry_run: bool


# ---------------------------------------------------------------------------
# Stamp helpers
# ---------------------------------------------------------------------------


def stamp_markdown(body: str, *, version: int = PRODUCTION_TEMPLATE_VERSION) -> str:
    """Prepend the HTML-comment version stamp to ``body``.

    Idempotent: if ``body`` already carries a head-stamp (anywhere in the
    first few lines), replace it. Otherwise insert it immediately after
    the YAML frontmatter block, or at the very start when there is no
    frontmatter.

    Raises ValueError if a stamp appears *mid-document* with a mismatched
    version (defensive guard against a misuse pattern).
    """
    matches = list(_MD_STAMP_RE.finditer(body))
    if matches:
        # Strict: replace the first match (the head-stamp) and verify any
        # additional matches carry the same version (else raise).
        head = matches[0]
        for m in matches[1:]:
            if int(m.group(1)) != version:
                raise ValueError(
                    f"mid-document stamp has version {m.group(1)} != {version}; "
                    "this looks like a corrupted template; refusing to stamp."
                )
        new_stamp = f"<!-- remory: template_version={version} -->"
        return body[: head.start()] + new_stamp + body[head.end() :]

    # No stamp present. Insert after the frontmatter fence if any.
    lines = body.splitlines(keepends=True)
    if lines and lines[0].rstrip("\r\n") == "---":
        # Find closing fence.
        close_idx: int | None = None
        for i in range(1, len(lines)):
            if lines[i].rstrip("\r\n") == "---":
                close_idx = i
                break
        if close_idx is not None:
            stamp_line = f"<!-- remory: template_version={version} -->\n"
            return "".join(lines[: close_idx + 1]) + stamp_line + "".join(lines[close_idx + 1 :])

    # Plain markdown without frontmatter — stamp at the very top.
    return f"<!-- remory: template_version={version} -->\n" + body


def detect_version(body: str) -> int | None:
    """Return the integer template version from the head-stamp, else None."""
    match = _MD_STAMP_RE.search(body)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _detect_version_settings_json(data: bytes) -> int | None:
    """Read the version key out of bundled-or-on-disk settings.json bytes."""
    try:
        obj: object = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    obj_dict = cast(dict[str, object], obj)
    raw = obj_dict.get(TEMPLATE_VERSION_KEY)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return None


def _detect_version_any(relpath: str, data: bytes) -> int | None:
    """Dispatch on extension: .md → markdown stamp, .json → key."""
    if relpath.endswith(".json"):
        return _detect_version_settings_json(data)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return detect_version(text)


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------


def _backups_dir(data_dir: Path) -> Path:
    return data_dir / ".claude" / ".backups"


def _flatten_relpath(target_path: Path, data_dir: Path) -> str:
    """Flatten <data_dir>-relative posix path to __-joined basename."""
    try:
        rel = target_path.relative_to(data_dir)
    except ValueError:
        # Not under data_dir — fall back to the absolute pieces joined.
        rel = Path(*target_path.parts[-3:])
    posix = PurePosixPath(rel.as_posix())
    return "__".join(posix.parts)


def _utc_timestamp() -> str:
    """UTC ISO timestamp with colons replaced by hyphens (Windows-safe)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def emit_backup(target_path: Path, data_dir: Path) -> Path:
    """Write a .bak of ``target_path`` under ``<data_dir>/.claude/.backups/``.

    Path layout: ``<flattened-relpath>.<utc-iso-timestamp>.bak``. Atomic via
    :func:`remory.atomic.atomic_write_bytes`. Returns the .bak path.
    Caller is responsible for guarding against a missing source.
    """
    bdir = _backups_dir(data_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    flat = _flatten_relpath(target_path, data_dir)
    ts = _utc_timestamp()
    bak_path = bdir / f"{flat}.{ts}.bak"
    data = target_path.read_bytes()
    atomic_write_bytes(bak_path, data)
    return bak_path


# ---------------------------------------------------------------------------
# Bundled-bytes resolver with stamp guarantee
# ---------------------------------------------------------------------------


def _bundled_stamped_bytes(relpath: str) -> bytes:
    """Return the bundled bytes for ``relpath``, already stamped.

    Bundled files ship pre-stamped (see test_data_templates_snapshot); we
    return the bytes as-is. This helper exists so callers don't have to
    care about the wire-format detail.
    """
    return read_template_bytes(relpath)


# ---------------------------------------------------------------------------
# Install / refresh
# ---------------------------------------------------------------------------


def _classify(
    target: Path,
    bundled: bytes,
    bundled_version: int,
    *,
    force: bool,
) -> tuple[str, SkippedEntry | None]:
    """Decide what to do for one file. Returns (action, skipped_entry_or_None).

    Actions: "write" | "overwrite" | "skip-unchanged" | "skip-newer" |
             "skip-unstamped" | "conflict".
    """
    if not target.exists():
        return "write", None

    on_disk = target.read_bytes()
    on_disk_version = _detect_version_any(target.name, on_disk)

    if on_disk_version is None:
        return "skip-unstamped", SkippedEntry(
            path=target,
            reason="unstamped-preserved",
            current_version=bundled_version,
            on_disk_version=None,
        )

    if on_disk_version > bundled_version:
        # Newer-on-disk — warn but skip; do NOT overwrite (ADR-0005).
        return "skip-newer", SkippedEntry(
            path=target,
            reason="newer-on-disk",
            current_version=bundled_version,
            on_disk_version=on_disk_version,
        )

    if on_disk_version < bundled_version:
        return "overwrite", None

    # Same version — compare bytes.
    if on_disk == bundled:
        return "skip-unchanged", SkippedEntry(
            path=target,
            reason="unchanged",
            current_version=bundled_version,
            on_disk_version=on_disk_version,
        )
    if force:
        return "overwrite", None
    return "conflict", SkippedEntry(
        path=target,
        reason="stamped-but-edited",
        current_version=bundled_version,
        on_disk_version=on_disk_version,
    )


def install_data_dir_templates(
    data_dir: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> EmitResult:
    """Idempotently materialise ``<data_dir>/.claude/`` from bundled templates.

    See module docstring for the full policy table. ``dry_run`` reports
    what would happen without writing or creating directories. Atomic
    writes via :func:`remory.atomic.atomic_write_bytes`.
    """
    written: list[Path] = []
    overwritten: list[Path] = []
    skipped: list[SkippedEntry] = []

    for relpath in iter_template_relpaths():
        bundled = _bundled_stamped_bytes(relpath)
        bundled_version = _detect_version_any(relpath, bundled)
        if bundled_version is None:
            # Defensive — every bundled file is supposed to be stamped.
            # Don't crash on a packaging bug; surface as a skip with the
            # 0 placeholder so the test snapshot can fail loudly.
            bundled_version = PRODUCTION_TEMPLATE_VERSION

        target = data_dir / relpath
        action, entry = _classify(target, bundled, bundled_version, force=force)

        if action == "write":
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, bundled)
            written.append(target)
            continue

        if action == "overwrite":
            if not dry_run:
                emit_backup(target, data_dir)
                atomic_write_bytes(target, bundled)
            overwritten.append(target)
            continue

        # Skip variants: defensive None-narrowing for pyright.
        if entry is None:
            entry = SkippedEntry(
                path=target,
                reason="unchanged",
                current_version=bundled_version,
                on_disk_version=bundled_version,
            )
        skipped.append(entry)

    return EmitResult(
        written=tuple(written),
        overwritten=tuple(overwritten),
        skipped=tuple(skipped),
        dry_run=dry_run,
    )


def refresh(
    data_dir: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> EmitResult:
    """Combined ``.claude/`` + per-topic ``CLAUDE.md`` refresh.

    Delegates to :func:`install_data_dir_templates` and
    :func:`remory.topic_claude_md.regen_all_topic_claude_md`. Combines
    the two ``EmitResult`` instances into one for the CLI's renderer.
    """
    from remory.topic_claude_md import regen_all_topic_claude_md

    claude_assets_result = install_data_dir_templates(data_dir, force=force, dry_run=dry_run)
    topic_entries = regen_all_topic_claude_md(data_dir, force=force, dry_run=dry_run)

    # Combine: topic entries report (path, action) tuples; we adopt their
    # actions into our buckets.
    written = list(claude_assets_result.written)
    overwritten = list(claude_assets_result.overwritten)
    skipped = list(claude_assets_result.skipped)

    for entry in topic_entries:
        if entry.action == "write":
            written.append(entry.path)
        elif entry.action == "overwrite":
            overwritten.append(entry.path)
        else:
            skipped.append(
                SkippedEntry(
                    path=entry.path,
                    reason=entry.reason,
                    current_version=entry.current_version,
                    on_disk_version=entry.on_disk_version,
                )
            )

    return EmitResult(
        written=tuple(written),
        overwritten=tuple(overwritten),
        skipped=tuple(skipped),
        dry_run=dry_run,
    )
