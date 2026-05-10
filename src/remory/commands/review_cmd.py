"""``remory review <topic>`` — print _review.md (last critique output)."""

from __future__ import annotations

import sys

from remory import config as cfgmod
from remory import paths
from remory.cli.errors import TopicMissingError

__all__ = ["run_review"]


def run_review(*, topic_name: str) -> None:
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

    review_path = paths.review_file(topic_dir)
    if not review_path.exists():
        sys.stdout.write(
            f"No review yet for '{topic_name}'. Run `remory sleep {topic_name}` first.\n",
        )
        return

    text = review_path.read_text(encoding="utf-8")
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
