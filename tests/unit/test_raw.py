"""Unit tests for ``remory.raw``."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from remory.locking import topic_lock
from remory.raw import (
    RawEntry,
    RawFrontmatter,
    RawSource,
    RawStatus,
    RawWriteError,
    _next_available_path,
    list_raw,
    mark_status,
    read_raw,
    write_raw,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock only")


def _make_topic(tmp_path: Path) -> Path:
    d = tmp_path / "topic"
    d.mkdir()
    return d


def _chat_fm(when: datetime, *, status: RawStatus = RawStatus.PENDING) -> RawFrontmatter:
    return RawFrontmatter(
        created=when,
        source=RawSource.CHAT,
        status=status,
        session_id="sess-001",
        duration_seconds=600,
    )


# ---------------------------------------------------------------------------
# Round-trip and locking
# ---------------------------------------------------------------------------


def test_round_trip_write_raw_and_read_raw_under_lock(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    fm = _chat_fm(datetime(2026, 5, 7, 18, 20, tzinfo=UTC))
    body = "**You:** hi\n**Remory:** hello\n"
    with topic_lock(d):
        path = write_raw(d, frontmatter=fm, body=body)
    assert path.name == "2026-05-07-1820.md"
    assert path.parent == d / "raw" / "2026"
    entry = read_raw(path)
    assert entry.frontmatter == fm
    assert entry.body == body


def test_write_raw_without_lock_raises_assertion_programming_bug(tmp_path: Path) -> None:
    """Programming-bug check: catches the caller forgetting to acquire the topic
    lock at all. NOT a defence against concurrent release between the assertion
    and the atomic rename.
    """
    d = _make_topic(tmp_path)
    fm = _chat_fm(datetime(2026, 5, 7, 18, 20, tzinfo=UTC))
    with pytest.raises(AssertionError, match="write_raw requires the topic lock"):
        write_raw(d, frontmatter=fm, body="x")


# ---------------------------------------------------------------------------
# Wire-format and validation
# ---------------------------------------------------------------------------


_NOW = datetime(2026, 5, 7, 18, 20, tzinfo=UTC)


@pytest.mark.parametrize(
    ("source", "with_session_id", "should_raise"),
    [
        (RawSource.CHAT, True, False),
        (RawSource.CHAT, False, True),
        (RawSource.INGESTED, True, True),
        (RawSource.INGESTED, False, False),
        (RawSource.EXTERNAL_TRANSCRIPT, True, True),
        (RawSource.EXTERNAL_TRANSCRIPT, False, False),
    ],
)
def test_session_id_required_iff_source_is_chat(
    source: RawSource, with_session_id: bool, should_raise: bool
) -> None:
    kwargs: dict[str, object] = {"created": _NOW, "source": source}
    if with_session_id:
        kwargs["session_id"] = "sess-xyz"
    if should_raise:
        with pytest.raises(ValidationError):
            RawFrontmatter(**kwargs)  # type: ignore[arg-type]  # parametrised dynamic kwargs
    else:
        fm = RawFrontmatter(**kwargs)  # type: ignore[arg-type]  # parametrised dynamic kwargs
        assert fm.source == source


def test_raw_status_and_source_value_sets_are_pinned() -> None:
    """Wire-format pin: changes to these value sets must follow the rules in
    the module docstring on ``remory.raw``.
    """
    assert {s.value for s in RawStatus} == {"pending", "consolidated", "archived"}, (
        "RawStatus value set drifted; see remory.raw module docstring for change rules"
    )
    assert {s.value for s in RawSource} == {"chat", "ingested", "external-transcript"}, (
        "RawSource value set drifted; see remory.raw module docstring for change rules"
    )


# ---------------------------------------------------------------------------
# Collision-suffix algorithm
# ---------------------------------------------------------------------------


def test_collision_suffix_no_collision_uses_base_name(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    fm = _chat_fm(datetime(2026, 5, 7, 18, 20, tzinfo=UTC))
    with topic_lock(d):
        p = write_raw(d, frontmatter=fm, body="first\n")
    assert p.name == "2026-05-07-1820.md"


def test_collision_suffix_one_collision_appends_dash_2(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    when = datetime(2026, 5, 7, 18, 20, tzinfo=UTC)
    fm1 = _chat_fm(when)
    fm2 = RawFrontmatter(
        created=when,
        source=RawSource.CHAT,
        session_id="sess-002",
    )
    with topic_lock(d):
        write_raw(d, frontmatter=fm1, body="first\n")
        p2 = write_raw(d, frontmatter=fm2, body="second\n")
    assert p2.name == "2026-05-07-1820-2.md"


def test_collision_suffix_two_collisions_appends_dash_3(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    when = datetime(2026, 5, 7, 18, 20, tzinfo=UTC)
    fms = [
        RawFrontmatter(created=when, source=RawSource.CHAT, session_id=f"s-{i}") for i in range(3)
    ]
    with topic_lock(d):
        results = [write_raw(d, frontmatter=fm, body=f"body {i}\n") for i, fm in enumerate(fms)]
    assert [p.name for p in results] == [
        "2026-05-07-1820.md",
        "2026-05-07-1820-2.md",
        "2026-05-07-1820-3.md",
    ]


def test_collision_suffix_at_limit_raises_RawWriteError(tmp_path: Path) -> None:
    """Use the ``_start_suffix`` test seam to avoid creating 100 real files."""
    d = _make_topic(tmp_path)
    year_dir = d / "raw" / "2026"
    year_dir.mkdir(parents=True)
    base = "2026-05-07-1820"
    # Pre-create the -99 slot. Combined with _start_suffix=99, the search
    # must exhaust without finding a free slot and raise.
    (year_dir / f"{base}-99.md").write_text("placeholder\n", encoding="utf-8")
    with pytest.raises(RawWriteError, match="more than 99 raw entries"):
        _next_available_path(d, base, _start_suffix=99)


# ---------------------------------------------------------------------------
# mark_status and list_raw
# ---------------------------------------------------------------------------


def test_mark_status_bulk_rewrites_all_entries_atomically(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    when = datetime(2026, 5, 7, 18, 20, tzinfo=UTC)
    paths_written: list[Path] = []
    with topic_lock(d):
        for i in range(3):
            fm = RawFrontmatter(created=when, source=RawSource.CHAT, session_id=f"s-{i}")
            paths_written.append(write_raw(d, frontmatter=fm, body=f"body {i}\n"))
        entries = [read_raw(p) for p in paths_written]
        rewritten = mark_status(entries, RawStatus.CONSOLIDATED)
    assert all(e.frontmatter.status == RawStatus.CONSOLIDATED for e in rewritten)
    re_read = [read_raw(p) for p in paths_written]
    assert all(e.frontmatter.status == RawStatus.CONSOLIDATED for e in re_read)


def test_list_raw_filters_by_status_and_year(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    with topic_lock(d):
        # Two entries in 2026, one in 2027.
        write_raw(
            d,
            frontmatter=RawFrontmatter(
                created=datetime(2026, 5, 7, 18, 20, tzinfo=UTC),
                source=RawSource.CHAT,
                session_id="a",
            ),
            body="a\n",
        )
        write_raw(
            d,
            frontmatter=RawFrontmatter(
                created=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
                source=RawSource.INGESTED,
                status=RawStatus.CONSOLIDATED,
            ),
            body="b\n",
        )
        write_raw(
            d,
            frontmatter=RawFrontmatter(
                created=datetime(2027, 1, 2, 3, 4, tzinfo=UTC),
                source=RawSource.CHAT,
                session_id="c",
            ),
            body="c\n",
        )
    all_entries = list_raw(d)
    assert len(all_entries) == 3
    by_year = list_raw(d, year=2026)
    assert len(by_year) == 2
    by_status = list_raw(d, status=RawStatus.CONSOLIDATED)
    assert len(by_status) == 1
    assert by_status[0].frontmatter.source == RawSource.INGESTED
    by_both = list_raw(d, year=2026, status=RawStatus.PENDING)
    assert len(by_both) == 1


def test_list_raw_sort_order_is_chronological_including_suffixed_slots(tmp_path: Path) -> None:
    """Numeric suffix sort: ``-9`` must sort before ``-10``."""
    d = _make_topic(tmp_path)
    year_dir = d / "raw" / "2026"
    year_dir.mkdir(parents=True)
    base = "2026-05-07-1820"
    # Create entries directly: base, -2, -9, -10 in shuffled write order.
    fm_shapes = {
        f"{base}.md": "first",
        f"{base}-2.md": "second",
        f"{base}-9.md": "ninth",
        f"{base}-10.md": "tenth",
    }
    for name, body_marker in fm_shapes.items():
        (year_dir / name).write_text(
            "---\n"
            "created: 2026-05-07T18:20:00Z\n"
            "source: chat\n"
            "status: pending\n"
            "session_id: sess-x\n"
            "---\n\n"
            f"{body_marker}\n",
            encoding="utf-8",
        )
    listed = list_raw(d)
    bodies = [e.body.strip() for e in listed]
    assert bodies == ["first", "second", "ninth", "tenth"]


# ---------------------------------------------------------------------------
# Architectural-rule test
# ---------------------------------------------------------------------------


def test_mark_status_signature_accepts_iterable_not_single_entry() -> None:
    """``mark_status`` is bulk-only: parameter is ``Iterable[RawEntry]``."""
    sig = inspect.signature(mark_status)
    annotation = sig.parameters["entries"].annotation
    # Annotation may be a string under ``from __future__ import annotations``;
    # accept either the textual form or the concrete generic alias.
    if isinstance(annotation, str):
        assert "Iterable" in annotation and "RawEntry" in annotation
    else:
        # ``Iterable[RawEntry]`` --- check origin and args explicitly.
        from typing import get_args, get_origin

        origin = get_origin(annotation)
        assert origin is Iterable or (
            origin is not None and getattr(origin, "__name__", None) == "Iterable"
        )
        args = get_args(annotation)
        assert RawEntry in args
