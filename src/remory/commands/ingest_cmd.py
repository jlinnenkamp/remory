"""``remory ingest <topic> <file>`` — add a markdown file as a raw entry.

The new raw entry is written with ``source: ingested`` and
``status: pending``. Acquires the topic lock for the write so it
interleaves correctly with chat/sleep.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from remory import config as cfgmod
from remory.cli.errors import TopicIncompleteError, TopicMissingError
from remory.locking import topic_lock
from remory.raw import RawFrontmatter, RawSource, RawStatus, write_raw
from remory.topic import load_topic, write_meta

__all__ = ["run_ingest"]


def run_ingest(*, topic_name: str, file: Path) -> None:
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

    if not (topic_dir / "meta.yaml").exists():
        raise TopicIncompleteError(topic_name, "meta.yaml missing")

    body = file.read_text(encoding="utf-8")
    if not body.strip():
        sys.stdout.write(f"File {file} is empty; nothing ingested.\n")
        return

    now = datetime.now(UTC)
    fm = RawFrontmatter(
        created=now,
        source=RawSource.INGESTED,
        status=RawStatus.PENDING,
    )
    with topic_lock(topic_dir, timeout=0.0):
        raw_path = write_raw(topic_dir, frontmatter=fm, body=body)
        topic = load_topic(topic_dir)
        new_meta = topic.meta.model_copy(
            update={
                "pending_count": topic.meta.pending_count + 1,
                "total_entries": topic.meta.total_entries + 1,
            }
        )
        write_meta(topic_dir, new_meta)
    sys.stdout.write(f"Ingested {file} as {raw_path}.\n")
