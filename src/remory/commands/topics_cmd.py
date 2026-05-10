"""``remory topics`` — list configured topics."""

from __future__ import annotations

from remory import config as cfgmod
from remory.topic import load_topic
from remory.ui import TopicsRow, print_topics_table

__all__ = ["run_topics"]


def run_topics() -> None:
    cfg = cfgmod.load_config()
    data_dir = cfgmod.resolve_data_dir(cfg)
    topics_root = data_dir / "topics"
    rows: list[TopicsRow] = []
    if topics_root.is_dir():
        for entry in sorted(topics_root.iterdir()):
            if not entry.is_dir():
                continue
            try:
                topic = load_topic(entry)
            except Exception:
                # Skip corrupt topics here; doctor surfaces them.
                continue
            rows.append(
                TopicsRow(
                    name=entry.name,
                    schema_name=topic.schema.name,
                    pending=topic.meta.pending_count,
                    last_chat=(topic.meta.last_chat.isoformat() if topic.meta.last_chat else "—"),
                    last_consolidated=(
                        topic.meta.last_consolidated.isoformat()
                        if topic.meta.last_consolidated
                        else "—"
                    ),
                ),
            )
    print_topics_table(rows)
