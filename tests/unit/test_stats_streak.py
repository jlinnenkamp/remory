"""Streak-helper tests for ``remory stats``.

The streak is the longest run of consecutive UTC days containing at least one
raw entry, of any source/status. UTC dates are the day boundary; today counts
when a recent entry exists.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remory.commands.stats_cmd import _compute_streak
from remory.locking import topic_lock
from remory.raw import RawFrontmatter, RawSource, RawStatus, write_raw

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock only")


def _seed_raw(topic_dir: Path, when: datetime) -> None:
    fm = RawFrontmatter(
        created=when,
        source=RawSource.CHAT,
        status=RawStatus.PENDING,
        session_id="s",
    )
    with topic_lock(topic_dir):
        write_raw(topic_dir, frontmatter=fm, body="b")


def test_stats_streak_returns_zero_for_empty_topic(tmp_path: Path) -> None:
    assert _compute_streak(tmp_path) == 0


def test_stats_streak_returns_one_for_single_day_entries(tmp_path: Path) -> None:
    _seed_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    _seed_raw(tmp_path, datetime(2026, 5, 9, 18, 0, tzinfo=UTC))
    assert _compute_streak(tmp_path) == 1


def test_stats_streak_returns_longest_consecutive_run_after_gap(tmp_path: Path) -> None:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    # Run A: days 1, 2, 3 (length 3).
    for d in (0, 1, 2):
        _seed_raw(tmp_path, base + timedelta(days=d))
    # Gap: days 3..6 inclusive empty.
    # Run B: days 7, 8, 9, 10, 11 (length 5) -- the longest.
    for d in (6, 7, 8, 9, 10):
        _seed_raw(tmp_path, base + timedelta(days=d))
    # Gap, then a single day -- shouldn't extend Run B.
    _seed_raw(tmp_path, base + timedelta(days=20))
    assert _compute_streak(tmp_path) == 5


def test_stats_streak_includes_today_when_recent_entry_exists(tmp_path: Path) -> None:
    """Today counts when a recent entry exists; a 14-day streak ending today reports 14."""
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    for d in range(14):
        _seed_raw(tmp_path, today - timedelta(days=d))
    assert _compute_streak(tmp_path) == 14


def test_stats_streak_uses_utc_date_boundary_not_local(tmp_path: Path) -> None:
    """Adjacent UTC days from non-UTC tz must be detected as consecutive."""
    from datetime import timezone

    tokyo = timezone(timedelta(hours=9))
    # 2026-05-09 01:30 +09:00 -> 2026-05-08 16:30 UTC -> UTC date May 8.
    _seed_raw(tmp_path, datetime(2026, 5, 9, 1, 30, tzinfo=tokyo))
    # 2026-05-10 01:30 +09:00 -> 2026-05-09 16:30 UTC -> UTC date May 9.
    _seed_raw(tmp_path, datetime(2026, 5, 10, 1, 30, tzinfo=tokyo))
    # UTC dates May 8 and May 9 are consecutive -> streak 2.
    assert _compute_streak(tmp_path) == 2
