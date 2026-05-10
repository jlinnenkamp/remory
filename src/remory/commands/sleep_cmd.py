"""``remory sleep`` — consolidate pending raw entries into ``state.md``.

Single-topic mode delegates to :func:`remory.sleep.orchestrator.sleep`.
``--if-due`` (CC3) iterates over all topics, including only those whose
``pending_count >= trigger_threshold``; per-topic sleeps are independent
and ``LockBusyError`` is bucketed into a per-topic FAIL line rather than
aborting the whole run.

Backend selection: defaults to :class:`ClaudeCodeBackend`. The
``backend_factory`` parameter is a test seam (and an extension point
for ``--backend`` if it ever lands).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from pathlib import Path

from remory import config as cfgmod
from remory.backends.base import Backend
from remory.backends.claude_code import ClaudeCodeBackend
from remory.cli.errors import TopicMissingError
from remory.locking import LockBusyError
from remory.sleep.orchestrator import SleepResult, sleep
from remory.topic import load_topic
from remory.ui import print_sleep_summary

__all__ = ["run_sleep"]

_log = logging.getLogger("remory.commands.sleep")


def _default_backend_factory() -> Backend:
    return ClaudeCodeBackend()


def _existing_topics(topics_root: Path) -> tuple[str, ...]:
    if not topics_root.is_dir():
        return ()
    return tuple(sorted(p.name for p in topics_root.iterdir() if p.is_dir()))


def run_sleep(
    *,
    topic_name: str | None,
    if_due: bool,
    dry_run: bool,
    backend_factory: Callable[[], Backend] | None = None,
) -> None:
    """Run a single-topic sleep, or iterate eligible topics under ``--if-due``.

    Args:
        topic_name: required unless ``if_due`` is True.
        if_due: when True, walks topics; raises if combined with a name.
        dry_run: passed through to the orchestrator.
        backend_factory: zero-arg callable returning a :class:`Backend`;
            defaults to :class:`ClaudeCodeBackend`.

    Raises:
        TopicMissingError: when ``topic_name`` is given and doesn't exist.
        LockBusyError, SleepError, ExtractError, MergeError, etc.: these
            propagate to the CLI surface for mapping in single-topic mode.
            Under ``--if-due``, per-topic ``LockBusyError`` is downgraded
            to a FAIL line and the iteration continues.
    """
    factory = backend_factory if backend_factory is not None else _default_backend_factory

    cfg = cfgmod.load_config()
    data_dir = cfgmod.resolve_data_dir(cfg)
    topics_root = data_dir / "topics"

    if if_due:
        _run_if_due(topics_root=topics_root, dry_run=dry_run, factory=factory)
        return

    if topic_name is None:
        # Typer enforces argument-or-option, but be defensive.
        raise TopicMissingError("", existing_topics=_existing_topics(topics_root))

    topic_dir = topics_root / topic_name
    if not topic_dir.exists():
        raise TopicMissingError(topic_name, existing_topics=_existing_topics(topics_root))

    backend = factory()
    result = sleep(topic_dir=topic_dir, backend=backend, dry_run=dry_run)
    print_sleep_summary(result)


def _run_if_due(
    *,
    topics_root: Path,
    dry_run: bool,
    factory: Callable[[], Backend],
) -> None:
    """Iterate topics under ``--if-due``.

    Eligibility: ``pending_count >= schema.sleep.trigger_threshold``.
    Per-topic ``LockBusyError`` becomes a FAIL line; other exceptions
    propagate (they'd indicate a data-shape or backend problem worth
    surfacing rather than swallowing).
    """
    backend = factory()
    if not topics_root.is_dir():
        # Use stdout because we want this to be visible without --verbose.
        sys.stdout.write("No topics yet. Run remory init to set one up.\n")
        return

    eligible: list[Path] = []
    for entry in sorted(topics_root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            topic = load_topic(entry)
        except Exception:
            _log.exception(
                "if_due: skipping topic %s due to load_topic failure",
                entry.name,
            )
            continue
        if topic.meta.pending_count >= topic.schema.sleep.trigger_threshold:
            eligible.append(entry)

    if not eligible:
        sys.stdout.write("No topics are at threshold. Nothing to do.\n")
        return

    results: list[tuple[str, str]] = []
    for topic_dir in eligible:
        try:
            result: SleepResult = sleep(topic_dir=topic_dir, backend=backend, dry_run=dry_run)
        except LockBusyError as exc:
            results.append((topic_dir.name, f"FAIL (lock busy: {exc})"))
            continue
        else:
            results.append((topic_dir.name, f"OK ({result.consolidated_count} consolidated)"))
            print_sleep_summary(result)

    sys.stdout.write("\n--- summary ---\n")
    for name, status in results:
        sys.stdout.write(f"  {name}: {status}\n")
