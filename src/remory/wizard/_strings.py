"""Locked user-facing strings for the wizard surface.

After Phase 6 the wizard's voice belongs to the ``wizard.md`` subagent;
the Python harness only owns a small set of byte-pinned messages: the
SIGINT pre/during-commit interrupts, the partial-failure templates, the
about-me failure message, the outro templates (singular/plural), and the
two new Phase 6 strings (preflight pointer at ``remory doctor``, and the
recovery-dir template).
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "ABOUT_ME_FAILURE_MESSAGE",
    "DURING_COMMIT_INTERRUPT_MESSAGE",
    "OUTRO_PLURAL_TEMPLATE",
    "OUTRO_SINGULAR_TEMPLATE",
    "PARTIAL_FAILURE_NO_PRIOR_TEMPLATE",
    "PARTIAL_FAILURE_WITH_PRIOR_TEMPLATE",
    "PRECONDITION_NEEDS_DOCTOR_MESSAGE",
    "PRE_COMMIT_INTERRUPT_MESSAGE",
    "RECOVERY_MESSAGE_TEMPLATE",
]


# ---------------------------------------------------------------------------
# Outro (pluralize-aware) — KEPT BYTE-STABLE from Phase 5
# ---------------------------------------------------------------------------

OUTRO_SINGULAR_TEMPLATE: Final[str] = (
    "You're set up.\n"
    "\n"
    "  data dir:        {data_dir}\n"
    "  topic:           {topic}\n"
    "  about-me.md:     {about_me_path}\n"
    "\n"
    "Try `remory chat {topic}` whenever you're ready. When the conversation\n"
    "feels done, run `remory sleep {topic}` to fold what you said into the\n"
    "topic's memory.\n"
    "\n"
    "If something looks off, `remory doctor` will tell you.\n"
)

OUTRO_PLURAL_TEMPLATE: Final[str] = (
    "You're set up.\n"
    "\n"
    "  data dir:        {data_dir}\n"
    "  topics:          {topics_csv}\n"
    "  about-me.md:     {about_me_path}\n"
    "\n"
    "Try `remory chat {first_topic}` whenever you're ready, or `remory chat\n"
    "{second_topic}` to start there. When the conversation feels done, run\n"
    "`remory sleep <topic>` to fold what you said into the topic's memory.\n"
    "\n"
    "If something looks off, `remory doctor` will tell you.\n"
)


# ---------------------------------------------------------------------------
# Interrupt + error messages — KEPT BYTE-STABLE from Phase 5
# ---------------------------------------------------------------------------

PRE_COMMIT_INTERRUPT_MESSAGE: Final[str] = (
    "Stopped. No files written. Run remory init when you're ready.\n"
)

DURING_COMMIT_INTERRUPT_MESSAGE: Final[str] = (
    "Stopped mid-write. Some files may exist. Run remory doctor to inspect.\n"
)

PARTIAL_FAILURE_WITH_PRIOR_TEMPLATE: Final[str] = (
    "Stopped mid-write at topic '{failed}'. Topic '{prior}' was created\n"
    "successfully. Run remory doctor to inspect, or remory init {failed} to\n"
    "retry the failed topic.\n"
)

PARTIAL_FAILURE_NO_PRIOR_TEMPLATE: Final[str] = (
    "Stopped mid-write at topic '{failed}'. Run remory doctor to inspect, or\n"
    "remory init {failed} to retry the failed topic.\n"
)

ABOUT_ME_FAILURE_MESSAGE: Final[str] = (
    "All topics created, but about-me.md couldn't be written. Run remory doctor.\n"
)


# ---------------------------------------------------------------------------
# NEW for Phase 6 (plan §5.9) — byte-stable from here
# ---------------------------------------------------------------------------

PRECONDITION_NEEDS_DOCTOR_MESSAGE: Final[str] = (
    "Remory needs the claude CLI to be installed and logged in before the wizard can run.\n"
    "Run: remory doctor\n"
    "Then re-run: remory init\n"
)

RECOVERY_MESSAGE_TEMPLATE: Final[str] = (
    "The wizard couldn't produce valid answers (tried twice).\n"
    "What you said is saved at:\n"
    "  {recovery_dir}\n"
    "No topic files were written. You can re-run `remory init` to try again.\n"
)
