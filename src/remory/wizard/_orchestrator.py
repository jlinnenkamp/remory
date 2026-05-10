"""Wizard orchestrator: drive welcome → … → outro and accumulate answers.

The orchestrator owns:

- The :class:`WizardAnswers` accumulator (Phase 4 dataclass shape).
- The linear step-walk (no back-navigation).
- The single LLM call (the letter) — hoisted here so step functions
  stay pure-I/O on stdin/stdout.

It does NOT own the COMMIT block; ``run_wizard`` returns the answers
+ letter and the caller (typically ``run_init`` empty-args path)
invokes :func:`remory.wizard._commit.commit`.

The class is internal (underscore prefix). Public entry point is
:func:`run_wizard` re-exported from :mod:`remory.wizard`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from rich.console import Console

from remory import config as cfgmod
from remory import paths
from remory.backends.base import Backend
from remory.backends.claude_code import ClaudeCodeBackend
from remory.ui import make_console
from remory.wizard import _steps
from remory.wizard import _strings as S
from remory.wizard._answers import WizardAnswers
from remory.wizard._commit import commit
from remory.wizard._letter import WizardAnswersForLetter, compose_letter

__all__ = ["run_wizard"]


def _default_backend_factory() -> Backend:
    return ClaudeCodeBackend()


class _WizardOrchestrator:
    """Drive the wizard interview. Returns ``(answers, letter)`` from :meth:`run`."""

    def __init__(
        self,
        *,
        console: Console,
        backend: Backend,
        data_dir: Path,
        input_fn: object | None = None,
    ) -> None:
        self._console = console
        self._backend = backend
        self._data_dir = data_dir
        self._input_fn = input_fn

    def run(self) -> tuple[WizardAnswers, str]:
        """Walk the linear interview. Returns ``(answers, letter_text)``.

        Does NOT commit — the caller invokes
        :func:`remory.wizard._commit.commit` (or equivalent) after
        confirming the answers.

        Pre-COMMIT :class:`KeyboardInterrupt` propagates up unchanged.
        """
        answers = WizardAnswers()

        _steps.step_welcome(console=self._console, data_dir_str=str(self._data_dir))
        answers.name = _steps.step_name(console=self._console, input_fn=self._input_fn)
        answers.chosen_topics = _steps.step_pick_topics(
            console=self._console,
            input_fn=self._input_fn,
        )
        for topic in answers.chosen_topics:
            tone = _steps.step_topic_q1(
                topic,
                console=self._console,
                input_fn=self._input_fn,
            )
            strictness = _steps.step_topic_q2(
                topic,
                console=self._console,
                input_fn=self._input_fn,
            )
            answers.knobs_by_topic[topic] = {"tone": tone, "strictness": strictness}
        answers.wish = _steps.step_wish(console=self._console, input_fn=self._input_fn)

        # Letter step — single LLM call, fallback on any BackendError.
        _steps.step_letter_precall(console=self._console)
        letter = compose_letter(
            WizardAnswersForLetter(
                name=answers.name,
                chosen_topics=tuple(answers.chosen_topics),
                knobs_by_topic=answers.knobs_by_topic,
                wish=answers.wish,
            ),
            backend=self._backend,
        )
        _steps.step_letter_lead_in(console=self._console, paragraph=letter)

        return answers, letter


def run_wizard(
    *,
    backend_factory: Callable[[], Backend] | None = None,
    console: Console | None = None,
    input_fn: object | None = None,
    data_dir: Path | None = None,
) -> None:
    """Run the interactive wizard end-to-end and write all artefacts.

    The CLI's ``remory init`` (no args) routes here. Drives the
    interview, calls the model for the letter (with fallback on any
    backend failure), then commits all artefacts via
    :func:`remory.wizard._commit.commit`. Finally renders the outro.

    Pre-COMMIT :class:`KeyboardInterrupt` is caught here, the locked
    "Stopped. No files written." message is printed to stderr, and
    the exception is re-raised so the CLI maps to exit 130. Errors
    that fire inside :func:`commit` (TopicExists, partial-failure,
    SIGINT-during-commit, about-me failure) propagate unchanged so
    ``cli/errors.py`` can render the locked branch wording.
    """
    eff_data_dir = data_dir if data_dir is not None else _resolve_data_dir()
    eff_backend = (backend_factory or _default_backend_factory)()
    eff_console = console if console is not None else make_console()

    orch = _WizardOrchestrator(
        console=eff_console,
        backend=eff_backend,
        data_dir=eff_data_dir,
        input_fn=input_fn,
    )

    try:
        answers, letter = orch.run()
    except KeyboardInterrupt:
        # Pre-COMMIT: nothing was written. Print the locked message to
        # stderr and re-raise for the CLI's exit-130 mapping.
        sys.stderr.write(S.PRE_COMMIT_INTERRUPT_MESSAGE)
        raise

    commit(answers, letter, data_dir=eff_data_dir)

    _steps.step_outro(
        console=eff_console,
        data_dir_str=str(eff_data_dir),
        chosen_topics=answers.chosen_topics,
        about_me_path_str=str(paths.about_me_file(eff_data_dir)),
    )


def _resolve_data_dir() -> Path:
    """Resolve the effective data directory at wizard entry.

    Mirrors :func:`remory.cli._resolve_data_dir_or_exit` but kept
    local to avoid pulling the CLI module into the wizard's import
    graph.
    """
    try:
        cfg = cfgmod.load_config()
    except cfgmod.ConfigError:
        return paths.data_dir()
    return cfgmod.resolve_data_dir(cfg)
