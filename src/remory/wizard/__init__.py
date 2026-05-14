"""Remory's first-run wizard.

Public surface:

- :class:`WizardAnswers` / :class:`WizardKnobs` — Pydantic wire-format
  models for what the ``wizard.md`` subagent writes
  (``answers.json``).
- :func:`run_wizard` — drive the claude-driven interview and commit
  artefacts.
- :func:`commit` — atomic-batch write of all topic dirs +
  ``about-me.md``. Exposed for tests and for the rare advanced caller
  that wants to drive the answers programmatically.
- Exception types raised from the wizard pipeline:
  :class:`WizardRedirectError` (the R3 "pass --schema" wording),
  :class:`WizardPreflightError` (claude binary or auth not OK),
  :class:`WizardAnswerParseError` (subagent output unparseable),
  :class:`WizardSubagentFailedError` (subagent exit non-zero or two-
  strike parse fail), :class:`WizardCommitPartialError` (mid-COMMIT
  failure), :class:`WizardAboutMeError` (about-me.md write failed
  after all topics committed), :class:`WizardSigintDuringCommitError`
  (SIGINT delivered during COMMIT).
- Backwards-compat aliases :class:`WizardNotBuiltError` /
  :data:`WIZARD_NOT_BUILT_MESSAGE` retained for one release; remove
  in v0.2 (R3 deprecation note).

Phase 6 rearchitected the wizard from Python-driven steps to a
claude-driven subagent (``wizard.md``); :class:`WizardThreeStrikesError`
and the underlying ``_steps.py`` / ``_letter.py`` / ``_validators.py``
modules were removed.
"""

from __future__ import annotations

from typing import Final

from remory.wizard._answers import WizardAnswers, WizardKnobs
from remory.wizard._commit import (
    WizardAboutMeError,
    WizardCommitPartialError,
    WizardSigintDuringCommitError,
    commit,
)
from remory.wizard._orchestrator import (
    WizardPreflightError,
    WizardSubagentFailedError,
    run_wizard,
)
from remory.wizard._subagent import WizardAnswerParseError

__all__ = [
    "WIZARD_NOT_BUILT_MESSAGE",
    "WIZARD_REDIRECT_MESSAGE",
    "WizardAboutMeError",
    "WizardAnswerParseError",
    "WizardAnswers",
    "WizardCommitPartialError",
    "WizardKnobs",
    "WizardNotBuiltError",
    "WizardPreflightError",
    "WizardRedirectError",
    "WizardSigintDuringCommitError",
    "WizardSubagentFailedError",
    "commit",
    "run_wizard",
]


# R3 wording — surfaced by the CLI's ``init`` callback when the user
# typed ``remory init <name>`` (no schema flag) but the topic does not
# already exist (which would route to D7). The Phase 4 message
# ("the wizard isn't built yet") is no longer accurate now that Phase
# 5 ships; the new wording redirects to either ``--schema`` or the
# no-args wizard.
WIZARD_REDIRECT_MESSAGE: Final[str] = (
    "Pass --schema to pick a built-in directly (--schema job-profile, "
    "--schema workout, --schema coaching), or run `remory init` with no "
    "arguments for the interactive wizard."
)


class WizardRedirectError(Exception):
    """Raised by ``init`` when a topic_name is given but ``--schema`` is missing.

    Carries the R3 user-facing message; the CLI maps it to exit 2.
    """


# Backwards-compat aliases (one-release deprecation per R3). Downstream
# code that imports the Phase 4 names continues to work; new code
# should reach for the new names.
WIZARD_NOT_BUILT_MESSAGE: Final[str] = WIZARD_REDIRECT_MESSAGE
WizardNotBuiltError = WizardRedirectError
