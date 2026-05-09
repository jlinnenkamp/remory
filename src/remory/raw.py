"""Raw-entry storage for a topic.

``RawStatus`` and ``RawSource`` are written into raw-entry frontmatter and
persist on disk indefinitely as part of users' personal data. They are
**wire format**, not internal enums.

Initial pinned values:

- ``RawStatus``: ``pending``, ``consolidated``, ``archived``.
- ``RawSource``: ``chat``, ``ingested``, ``external-transcript``.

Change rules:

- **Add** a value: requires a forward-compat plan --- older readers must
  tolerate the new value as unknown-but-loadable, OR the addition is gated
  on a ``schema_version`` bump.
- **Rename** a value: requires a one-shot migration over existing user files
  plus a deprecation window if any tooling reads the old value.
- **Remove** a value: both of the above.

Locking discipline matches :mod:`remory.topic` and :mod:`remory.state`:
``write_raw`` and ``mark_status`` assert ``is_locked`` on the topic
directory. The assertion is a programming-bug check (caller forgot to
acquire the lock at all), not a defence against concurrent release.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from remory import paths
from remory.atomic import atomic_write_text
from remory.locking import is_locked

__all__ = [
    "RawEntry",
    "RawFrontmatter",
    "RawParseError",
    "RawSource",
    "RawStatus",
    "RawWriteError",
    "list_raw",
    "mark_status",
    "read_raw",
    "write_raw",
]


class RawStatus(StrEnum):
    PENDING = "pending"
    CONSOLIDATED = "consolidated"
    ARCHIVED = "archived"


class RawSource(StrEnum):
    CHAT = "chat"
    INGESTED = "ingested"
    EXTERNAL_TRANSCRIPT = "external-transcript"


class RawParseError(Exception):
    """Raised when a raw entry file is malformed."""


class RawWriteError(Exception):
    """Raised when a raw entry cannot be written (e.g. collision overflow)."""


class RawFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: datetime
    source: RawSource
    status: RawStatus = RawStatus.PENDING
    session_id: str | None = None
    duration_seconds: int | None = Field(ge=0, default=None)

    @model_validator(mode="after")
    def _session_id_required_iff_chat(self) -> RawFrontmatter:
        if self.source == RawSource.CHAT and self.session_id is None:
            raise ValueError("session_id is required when source == 'chat'")
        if self.source != RawSource.CHAT and self.session_id is not None:
            raise ValueError("session_id is only allowed when source == 'chat'")
        return self


@dataclass(frozen=True)
class RawEntry:
    path: Path
    frontmatter: RawFrontmatter
    body: str


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------


_RAW_FILENAME_RE = re.compile(r"^(?P<minute>\d{4}-\d{2}-\d{2}-\d{4})(?:-(?P<suffix>\d+))?\.md$")


def _base_name_for(created: datetime) -> str:
    """Return ``YYYY-MM-DD-HHMM`` for ``created`` (UTC)."""
    return created.strftime("%Y-%m-%d-%H%M")


def _next_available_path(
    topic_dir: Path,
    base_name: str,
    *,
    _start_suffix: int = 2,
) -> Path:
    """Return the next free ``raw/<year>/<base>[-<n>].md`` path in ``topic_dir``.

    Suffix format: single dash + decimal integer (no zero-padding) starting
    at 2. ``-1`` is never used. Raises :class:`RawWriteError` once ``-99``
    is also taken.

    The ``_start_suffix`` parameter is a test seam: the collision-limit
    test passes ``_start_suffix=99`` so it does not need to write 100 real
    files to provoke the overflow.
    """
    year = int(base_name[:4])
    year_dir = paths.raw_year_dir(topic_dir, year)

    base_path = year_dir / f"{base_name}.md"
    if _start_suffix == 2 and not base_path.exists():
        return base_path

    start = max(_start_suffix, 2)
    for n in range(start, 100):
        candidate = year_dir / f"{base_name}-{n}.md"
        if not candidate.exists():
            return candidate
    raise RawWriteError(
        f"more than 99 raw entries in the same UTC minute for topic "
        f"{topic_dir.name!r}; refusing to write"
    )


# ---------------------------------------------------------------------------
# Frontmatter (de)serialisation
# ---------------------------------------------------------------------------


def _format_frontmatter(fm: RawFrontmatter) -> str:
    raw = fm.model_dump(mode="json")
    # Drop None fields for a tidy on-disk representation; they round-trip
    # fine because the model defaults are None.
    cleaned = {k: v for k, v in raw.items() if v is not None}
    if isinstance(cleaned.get("created"), str) and cleaned["created"].endswith("+00:00"):
        cleaned["created"] = cleaned["created"][: -len("+00:00")] + "Z"
    return yaml.safe_dump(
        cleaned,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def _serialise(fm: RawFrontmatter, body: str) -> str:
    body_normalised = body if body.endswith("\n") else body + "\n"
    return f"---\n{_format_frontmatter(fm)}---\n\n{body_normalised}"


def _split_frontmatter(text: str, source_path: Path) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise RawParseError(f"raw entry {source_path} must start with a '---' frontmatter fence")
    close_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            close_idx = i
            break
    if close_idx is None:
        raise RawParseError(f"raw entry {source_path} is missing the closing '---' fence")
    yaml_text = "".join(lines[1:close_idx])
    body = "".join(lines[close_idx + 1 :])
    # Strip a single leading blank line, if present, to mirror serialisation.
    if body.startswith("\n"):
        body = body[1:]
    return yaml_text, body


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_raw(
    topic_dir: Path,
    *,
    frontmatter: RawFrontmatter,
    body: str,
) -> Path:
    """Atomically write a new raw entry, returning its path.

    Filename is ``YYYY-MM-DD-HHMM.md`` from ``frontmatter.created`` (UTC).
    On collision, the suffixes ``-2``, ``-3``, ... ``-99`` are tried in order;
    overflow raises :class:`RawWriteError`.
    """
    assert is_locked(topic_dir), "write_raw requires the topic lock"
    base_name = _base_name_for(frontmatter.created)
    year = int(base_name[:4])
    year_dir = paths.raw_year_dir(topic_dir, year)
    year_dir.mkdir(parents=True, exist_ok=True)
    target = _next_available_path(topic_dir, base_name)
    atomic_write_text(target, _serialise(frontmatter, body))
    return target


def read_raw(path: Path) -> RawEntry:
    """Read and validate a raw entry file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RawParseError(f"could not read {path}: {exc}") from exc
    yaml_text, body = _split_frontmatter(text, path)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise RawParseError(f"frontmatter YAML parse error in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RawParseError(f"frontmatter in {path} must be a mapping")
    try:
        fm = RawFrontmatter.model_validate(data)
    except ValidationError as exc:
        raise RawParseError(f"frontmatter validation error in {path}: {exc}") from exc
    return RawEntry(path=path, frontmatter=fm, body=body)


def _sort_key(path: Path) -> tuple[str, int]:
    match = _RAW_FILENAME_RE.match(path.name)
    if match is None:
        # Unknown shape sorts last; preserves stable behaviour but users
        # should not be writing such files into raw/.
        return (path.name, 0)
    suffix_text = match.group("suffix")
    suffix = int(suffix_text) if suffix_text is not None else 1
    return (match.group("minute"), suffix)


def list_raw(
    topic_dir: Path,
    *,
    status: RawStatus | None = None,
    year: int | None = None,
) -> list[RawEntry]:
    """List raw entries in ``topic_dir``, sorted chronologically.

    Sort order is by ``(minute, suffix)``. The unsuffixed file in a minute
    sorts before any ``-N.md`` slot for that minute, and ``-9`` sorts before
    ``-10`` because the suffix is compared as an integer.
    """
    raw_dir = topic_dir / "raw"
    if not raw_dir.is_dir():
        return []

    if year is not None:
        candidate_dirs = [raw_dir / str(year)]
    else:
        candidate_dirs = sorted([d for d in raw_dir.iterdir() if d.is_dir()])

    entries: list[RawEntry] = []
    for year_dir in candidate_dirs:
        if not year_dir.is_dir():
            continue
        for path in sorted(year_dir.glob("*.md"), key=_sort_key):
            entry = read_raw(path)
            if status is not None and entry.frontmatter.status != status:
                continue
            entries.append(entry)
    return entries


def mark_status(entries: Iterable[RawEntry], new_status: RawStatus) -> list[RawEntry]:
    """Bulk: rewrite each entry's status atomically.

    The topic lock must be held for every entry's parent topic directory.
    The topic-dir resolution walks two parents up: ``raw/<year>/<file>``.
    Returns the rewritten entries (in input order).
    """
    materialised = list(entries)
    # Group locking assertions: assert_is_locked once per topic dir.
    seen_topic_dirs: set[Path] = set()
    for entry in materialised:
        topic_dir = entry.path.parent.parent.parent
        if topic_dir in seen_topic_dirs:
            continue
        assert is_locked(topic_dir), f"mark_status requires the topic lock for {topic_dir}"
        seen_topic_dirs.add(topic_dir)

    rewritten: list[RawEntry] = []
    for entry in materialised:
        new_fm = entry.frontmatter.model_copy(update={"status": new_status})
        atomic_write_text(entry.path, _serialise(new_fm, entry.body))
        rewritten.append(RawEntry(path=entry.path, frontmatter=new_fm, body=entry.body))
    return rewritten
