"""``remory recent <topic>`` — list the last N raw entries.

Read-only (CC9): no lock; lists by filename order then prints a brief
summary line per entry plus a path so the user can ``cat`` it.
"""

from __future__ import annotations

import sys

from remory import config as cfgmod
from remory.cli.errors import TopicMissingError
from remory.raw import RawStatus, list_raw

__all__ = ["run_recent"]


def run_recent(*, topic_name: str, n: int = 5) -> None:
    cfg = cfgmod.load_config()
    data_dir = cfgmod.resolve_data_dir(cfg)
    topics_root = data_dir / "topics"
    topic_dir = topics_root / topic_name
    if not topic_dir.exists():
        existing = tuple(
            sorted(p.name for p in topics_root.iterdir() if p.is_dir())
            if topics_root.is_dir()
            else ()
        )
        raise TopicMissingError(topic_name, existing_topics=existing)

    entries = list_raw(topic_dir, status=None)
    if not entries:
        sys.stdout.write(f"No raw entries yet for '{topic_name}'.\n")
        return

    last = entries[-n:]
    for entry in last:
        status = entry.frontmatter.status
        marker = "*" if status is RawStatus.PENDING else " "
        sys.stdout.write(
            f" {marker} {entry.frontmatter.created.isoformat()}  "
            f"{entry.frontmatter.source}  {entry.path}\n"
        )
