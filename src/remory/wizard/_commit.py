"""Wizard COMMIT block — write all topic dirs + about-me.md atomically.

This module owns:

- :func:`commit` — the orchestrator's COMMIT entry point.
- :func:`_deferred_sigint` — a context manager that masks SIGINT for
  the duration of the block (POSIX strict; Windows best-effort per ADR
  0004).
- :func:`_about_me_bytes` — pure renderer for the ``about-me.md``
  format pinned in the consolidated plan §6.

Locking: each topic acquires its own ``topic_lock(timeout=0.0)``. The
lock is released between topics; failures partway through leave prior
topics intact (ADR 0003 leave-as-is).

Per-write granularity for SIGINT deferral: every ``atomic_write_*`` /
``write_meta`` / ``write_state`` call is wrapped in a fresh
:func:`_deferred_sigint` block. The window is bounded to a single
file's I/O.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import sys
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

from remory import paths
from remory.atomic import atomic_write_text
from remory.locking import topic_lock
from remory.schema import Schema, load_builtin
from remory.state import StateDoc, StateFrontmatter, StateSection, write_state
from remory.templates import CLAUDE_MD_PLACEHOLDER
from remory.topic import Knobs, TopicMeta, write_meta
from remory.wizard._answers import WizardAnswers

__all__ = [
    "WizardAboutMeError",
    "WizardCommitPartialError",
    "WizardSigintDuringCommitError",
    "commit",
]

_log = logging.getLogger("remory.wizard.commit")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WizardCommitPartialError(Exception):
    """COMMIT failed partway. Carries the failed topic + optional prior topic.

    ``prior_topic`` is the most recently completed topic before the
    failure; ``None`` if the failure happened on the very first topic
    (no prior progress to mention).
    """

    def __init__(
        self,
        *,
        failed_topic: str,
        prior_topic: str | None,
        cause: BaseException | None = None,
    ) -> None:
        self.failed_topic = failed_topic
        self.prior_topic = prior_topic
        self.cause = cause
        super().__init__(
            f"wizard COMMIT failed at topic {failed_topic!r}"
            + (f" (prior: {prior_topic!r})" if prior_topic else "")
        )


class WizardAboutMeError(Exception):
    """All topics committed, but ``about-me.md`` could not be written."""


class WizardSigintDuringCommitError(Exception):
    """SIGINT delivered while the COMMIT block was running.

    Raised after the ``_deferred_sigint`` block unmasks and the queued
    signal lands as a :class:`KeyboardInterrupt`. The wizard's CLI
    surface (``cli/errors.py``) maps this to exit 130 with the locked
    "Stopped mid-write…" message.
    """


# ---------------------------------------------------------------------------
# SIGINT deferral
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _deferred_sigint() -> Generator[None]:
    """Mask SIGINT for the duration of the block.

    POSIX: uses :func:`signal.pthread_sigmask`. The kernel queues any
    SIGINT delivered while masked; on unmask the signal is delivered
    immediately and propagates as :class:`KeyboardInterrupt` to the
    caller.

    Windows: ``pthread_sigmask`` is unavailable; fall back to a
    flag-based handler. Best-effort per ADR 0004.

    Re-entrance: the body is expected to be a single small atomic
    write. Nested deferral is not needed; we still preserve the prior
    SIGINT handler on Windows so re-entry would be safe if a future
    caller needs it.
    """
    if sys.platform == "win32":
        # Best-effort: install a flag-based handler. Known race window
        # (ADR 0004) — a SIGINT delivered between the flag check and
        # the actual write may interrupt the write at the OS level.
        interrupted = False

        def _flag_handler(signum: int, frame: object) -> None:
            del signum, frame
            nonlocal interrupted
            interrupted = True

        prev = signal.signal(signal.SIGINT, _flag_handler)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, prev)
            if interrupted:
                raise KeyboardInterrupt
        return

    # POSIX strict path.
    prev_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGINT])
    try:
        yield
    finally:
        # Restore the previous mask. If SIGINT was not previously
        # blocked, it will be delivered now — which raises
        # KeyboardInterrupt in this thread.
        signal.pthread_sigmask(signal.SIG_SETMASK, prev_mask)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_meta(answers: WizardAnswers, topic_name: str, schema: Schema) -> TopicMeta:
    """Build :class:`TopicMeta` from answers + schema for one topic.

    Knobs come from ``answers.knobs_by_topic[topic_name]`` if the user
    provided them, else from the schema's defaults.
    """
    user_knobs = answers.knobs_by_topic.get(topic_name, {})
    tone_raw = user_knobs.get("tone") or schema.defaults.tone
    strictness_raw = user_knobs.get("strictness") or schema.defaults.strictness
    # The Pydantic model validates against the Literal sets; mypy/pyright
    # narrow correctly because ``Knobs`` itself enforces the literal.
    knobs = Knobs.model_validate({"tone": tone_raw, "strictness": strictness_raw})
    return TopicMeta(
        schema=topic_name,
        schema_version=schema.version,
        created=datetime.now(UTC),
        last_consolidated=None,
        last_chat=None,
        pending_count=0,
        total_entries=0,
        knobs=knobs,
    )


def _build_state_doc(topic_name: str, schema: Schema) -> StateDoc:
    """Build a fresh :class:`StateDoc` skeleton for one topic."""
    return StateDoc(
        frontmatter=StateFrontmatter(
            schema=topic_name,
            schema_version=schema.version,
            last_consolidated=None,
            entries_consolidated=0,
        ),
        sections=[StateSection(title=section.title, body="\n") for section in schema.sections],
    )


def _about_me_bytes(answers: WizardAnswers, letter: str) -> str:
    """Render ``about-me.md`` per the consolidated plan §6 byte format.

    Format:

        {letter_paragraph}

        ---
        name: {name_or_blank}
        topics: {topics_csv}
        wish: {wish_or_blank}

    Trailing newline at EOF. Topics in selection order. Empty values
    after the colons when name/wish are unset (still useful: the
    letter paragraph carries the meaningful content).
    """
    name_value = answers.name if answers.name is not None else ""
    wish_value = answers.wish if answers.wish is not None else ""
    topics_csv = ", ".join(answers.chosen_topics)
    return f"{letter}\n\n---\nname: {name_value}\ntopics: {topics_csv}\nwish: {wish_value}\n"


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


def commit(
    answers: WizardAnswers,
    letter: str,
    *,
    data_dir: Path,
) -> None:
    """Atomically materialise ``answers`` + ``letter`` to disk.

    Order:

    1. ``mkdir`` data_dir + topics_dir.
    2. For each topic in selection order: acquire topic_lock, write
       meta.yaml, state.md, CLAUDE.md (each wrapped in
       ``_deferred_sigint``).
    3. Write about-me.md at the data-dir root (also deferred).

    Raises:
        TopicExistsError: a chosen topic dir already exists. ADR 0003
            leave-as-is means this fires inside the COMMIT block (per
            §2 #2), not at the validator.
        WizardCommitPartialError: any write failed mid-COMMIT. Carries
            the failed topic and the most recent prior-completed topic.
        WizardAboutMeError: about-me.md write failed after all topics
            committed.
        WizardSigintDuringCommitError: SIGINT delivered during the
            COMMIT block. The in-flight write completed; subsequent
            files were not written.
    """
    # Imported lazily to avoid a wizard ↔ cli.errors circular import:
    # cli.errors imports the wizard exception types we re-export from
    # __init__. Keeping TopicExistsError off the module-level import
    # graph here lets both packages load.
    from remory.cli.errors import TopicExistsError

    # We resolve topics root from data_dir directly rather than calling
    # paths.topics_dir() — the latter reads $REMORY_DATA_DIR/XDG, but
    # the wizard's caller has already resolved data_dir for us.
    data_dir.mkdir(parents=True, exist_ok=True)
    topics_root = data_dir / "topics"
    topics_root.mkdir(parents=True, exist_ok=True)

    completed_topics: list[str] = []
    for topic_name in answers.chosen_topics:
        topic_dir = topics_root / topic_name
        if topic_dir.exists():
            raise TopicExistsError(name=topic_name, topic_dir=topic_dir)

        try:
            schema = load_builtin(topic_name)
            topic_dir.mkdir(parents=False, exist_ok=False)
            with topic_lock(topic_dir, timeout=0.0):
                with _deferred_sigint():
                    write_meta(topic_dir, _build_meta(answers, topic_name, schema))
                with _deferred_sigint():
                    write_state(
                        paths.state_file(topic_dir),
                        _build_state_doc(topic_name, schema),
                    )
                with _deferred_sigint():
                    atomic_write_text(
                        paths.claude_md_file(topic_dir),
                        CLAUDE_MD_PLACEHOLDER.format(schema_name=topic_name),
                    )
        except KeyboardInterrupt as ki:
            # SIGINT was deferred while a write was in-flight; the
            # write completed, then unmask delivered the signal here.
            # Surface as the wizard-specific exception so the CLI can
            # render the locked mid-write message (exit 130).
            raise WizardSigintDuringCommitError(
                f"SIGINT during commit at topic {topic_name!r}"
            ) from ki
        except TopicExistsError:
            # Re-raise unchanged — this is a refusal at COMMIT, not a
            # mid-write failure. cli/errors.py maps to D7 wording.
            raise
        except Exception as exc:
            prior = completed_topics[-1] if completed_topics else None
            raise WizardCommitPartialError(
                failed_topic=topic_name,
                prior_topic=prior,
                cause=exc,
            ) from exc
        completed_topics.append(topic_name)

    # All topics done — write about-me.md.
    try:
        with _deferred_sigint():
            atomic_write_text(
                paths.about_me_file(data_dir),
                _about_me_bytes(answers, letter),
            )
    except KeyboardInterrupt as ki:
        raise WizardSigintDuringCommitError("SIGINT during commit at about-me.md") from ki
    except Exception as exc:
        raise WizardAboutMeError(f"all topics created but about-me.md failed: {exc}") from exc
