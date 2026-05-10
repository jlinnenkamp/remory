"""``remory state <topic>`` — print state.md for a topic to stdout.

Read-only (CC9): no lock acquired; relies on the atomic-write contract
shipping a complete state.md or none.
"""

from __future__ import annotations

import sys

from remory import config as cfgmod
from remory import paths
from remory.cli.errors import TopicIncompleteError, TopicMissingError

__all__ = ["run_state"]


def run_state(*, topic_name: str) -> None:
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

    state_path = paths.state_file(topic_dir)
    if not state_path.exists():
        raise TopicIncompleteError(topic_name, "state.md missing")

    text = state_path.read_text(encoding="utf-8")
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
