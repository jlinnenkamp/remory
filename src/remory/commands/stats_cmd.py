"""``remory stats`` -- cross-topic stats: entries, last sleep, simple streaks.

Streak rule (user-pinned, see Phase 4 plan):
    The streak is the longest run of consecutive UTC days containing AT LEAST
    ONE raw entry of any source/status. Today counts when a recent entry exists.
    UTC dates are the day boundary, matching Phase 3's append-only-format
    convention.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

from remory import config as cfgmod
from remory.raw import list_raw
from remory.topic import load_topic

__all__ = ["run_stats"]


def _compute_streak(topic_dir: Path) -> int:
    """Longest run of consecutive UTC days with at least one raw entry.

    Empty topic -> 0. Single-day -> 1. Gap-and-resume returns the longest
    segment, not the most recent.
    """
    entries = list_raw(topic_dir, status=None)
    if not entries:
        return 0
    days = sorted({e.frontmatter.created.astimezone(UTC).date() for e in entries})
    longest = 1
    current = 1
    for prev, curr in pairwise(days):
        if curr - prev == timedelta(days=1):
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest


def _render_last_sleep(when: datetime | None, today: date) -> str:
    if when is None:
        return "never"
    delta = (today - when.astimezone(UTC).date()).days
    if delta <= 0:
        return "today"
    if delta == 1:
        return "1 day ago"
    return f"{delta} days ago"


def run_stats() -> None:
    cfg = cfgmod.load_config()
    data_dir = cfgmod.resolve_data_dir(cfg)
    topics_root = data_dir / "topics"
    if not topics_root.is_dir():
        sys.stdout.write("No topics yet. Run remory init to set one up.\n")
        return

    today = datetime.now(UTC).date()
    rows: list[tuple[str, int, int, str, str]] = []
    total_entries = 0

    for entry in sorted(topics_root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            topic = load_topic(entry)
        except Exception:
            continue
        streak = _compute_streak(entry)
        rows.append(
            (
                topic.name,
                topic.meta.total_entries,
                topic.meta.pending_count,
                _render_last_sleep(topic.meta.last_consolidated, today),
                f"{streak} days",
            )
        )
        total_entries += topic.meta.total_entries

    if not rows:
        sys.stdout.write("No topics yet. Run remory init to set one up.\n")
        return

    headers = ("topic", "entries", "pending", "last sleep", "streak")
    widths = [max(len(headers[i]), max(len(str(r[i])) for r in rows)) for i in range(len(headers))]
    fmt = (
        f"{{:<{widths[0]}}}  "
        f"{{:>{widths[1]}}}  "
        f"{{:>{widths[2]}}}  "
        f"{{:<{widths[3]}}}  "
        f"{{:>{widths[4]}}}\n"
    )
    sys.stdout.write(fmt.format(*headers))
    for r in rows:
        sys.stdout.write(fmt.format(r[0], str(r[1]), str(r[2]), r[3], r[4]))
    sys.stdout.write("\n")
    plural = "s" if len(rows) != 1 else ""
    sys.stdout.write(f"{len(rows)} topic{plural}, {total_entries} entries total.\n")
