"""Locked user-facing strings for the Phase 5 wizard.

The strings in this module are byte-stable per Phase 4 consolidated
plan §2 (which §3 locks). Phase 5 implements; it does not redesign.
Edits here require updating the binding plan first.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "ABOUT_ME_FAILURE_MESSAGE",
    "COACHING_PREAMBLE",
    "COACHING_Q1",
    "COACHING_Q2",
    "DURING_COMMIT_INTERRUPT_MESSAGE",
    "JOB_PROFILE_PREAMBLE",
    "JOB_PROFILE_Q1",
    "JOB_PROFILE_Q2",
    "LETTER_LEAD_IN",
    "LETTER_PRECALL",
    "OUTRO_PLURAL_TEMPLATE",
    "OUTRO_SINGULAR_TEMPLATE",
    "PARTIAL_FAILURE_NO_PRIOR_TEMPLATE",
    "PARTIAL_FAILURE_WITH_PRIOR_TEMPLATE",
    "PICK_TOPICS_PROMPT",
    "PRE_COMMIT_INTERRUPT_MESSAGE",
    "STEP_NAME_PROMPT",
    "STEP_WISH_PROMPT",
    "THREE_STRIKES_MESSAGE",
    "WELCOME_TEMPLATE",
    "WORKOUT_PREAMBLE",
    "WORKOUT_Q1",
    "WORKOUT_Q2",
]


# ---------------------------------------------------------------------------
# §3.1 Welcome
# ---------------------------------------------------------------------------

WELCOME_TEMPLATE: Final[str] = (
    "Remory.\n"
    "\n"
    "A second brain that actually remembers — but only the bits you bring it.\n"
    "Your data will live at: {data_dir}\n"
    "\n"
    "This first run takes about three minutes. Two short questions for each\n"
    "topic you pick. You can skip any of them; I'll use a sensible default.\n"
    "\n"
    "Press Ctrl+C any time. Nothing is written until the very end, and if\n"
    "you stop partway, nothing is left behind.\n"
)


# ---------------------------------------------------------------------------
# §3.2 Step 1 — Name
# ---------------------------------------------------------------------------

STEP_NAME_PROMPT: Final[str] = "What should I call you?\n> "


# ---------------------------------------------------------------------------
# §3.3 Step 2 — Pick topics
# ---------------------------------------------------------------------------

PICK_TOPICS_PROMPT: Final[str] = (
    "Which of these would you like to set up? (You can add more later.)\n"
    "\n"
    "  [1] job-profile  — career direction; interviews and self-reflection\n"
    "                     accumulate into an evolving picture.\n"
    "  [2] workout      — a living plan plus session logs; adapts as you do.\n"
    "  [3] coaching     — therapy and coaching insights, gathered without\n"
    "                     pushing interpretations.\n"
    "\n"
    "Pick one or more by number, separated by commas. Press Enter for all three.\n"
    "> "
)


# ---------------------------------------------------------------------------
# §3.4.1 job-profile
# ---------------------------------------------------------------------------

JOB_PROFILE_PREAMBLE: Final[str] = (
    "job-profile — career direction.\n"
    "\n"
    "Two short questions and you're done. The point of this topic is to\n"
    "notice what you actually want from work over time, not to give you\n"
    "advice on the spot.\n"
)

JOB_PROFILE_Q1: Final[str] = (
    "When you say something contradictory across sessions, do you want me to\n"
    "gently flag it, or pretend I didn't notice?\n"
    "\n"
    "  [1] Gently flag, with care\n"
    "  [2] Just call it out\n"
    '  [s] Skip — use the default ("Gently flag, with care")\n'
    "\n"
    "> "
)

JOB_PROFILE_Q2: Final[str] = (
    "How rigorous should I be when assessing a job option you bring up?\n"
    "\n"
    "  [1] Encouraging\n"
    "  [2] Stress-test it\n"
    '  [s] Skip — use the default ("Encouraging")\n'
    "\n"
    "> "
)


# ---------------------------------------------------------------------------
# §3.4.2 workout (R1 wording on Q2)
# ---------------------------------------------------------------------------

WORKOUT_PREAMBLE: Final[str] = (
    "workout — your living training plan.\n"
    "\n"
    "Two short questions and you're done. I won't program for you;\n"
    "I'll just hold the plan and what you actually did, and notice\n"
    "when those drift apart.\n"
)

WORKOUT_Q1: Final[str] = (
    "When a session goes badly, do you want me warm about it, or do you\n"
    "want me to just say what I see?\n"
    "\n"
    "  [1] Warm; meet me where I am\n"
    "  [2] Direct; just tell me\n"
    '  [s] Skip — use the default ("Direct; just tell me")\n'
    "\n"
    "> "
)

WORKOUT_Q2: Final[str] = (
    "How strict should I be about programming and progression?\n"
    "\n"
    "  [1] Lenient; life happens\n"
    "  [2] Hold me to the plan\n"
    '  [s] Skip — leave it at the default for now ("balanced")\n'
    "\n"
    "> "
)


# ---------------------------------------------------------------------------
# §3.4.3 coaching
# ---------------------------------------------------------------------------

COACHING_PREAMBLE: Final[str] = (
    "coaching — a quiet place for what comes up in therapy or coaching.\n"
    "\n"
    "Two short questions. I won't play therapist with you. I'll just\n"
    "hold themes lightly, and not push interpretations you haven't\n"
    "arrived at yourself.\n"
)

COACHING_Q1: Final[str] = (
    "How do you want me to hold what you bring here — close and warm,\n"
    "or measured and a bit cooler?\n"
    "\n"
    "  [1] Close and warm\n"
    "  [2] Measured and steady\n"
    '  [s] Skip — use the default ("Close and warm")\n'
    "\n"
    "> "
)

COACHING_Q2: Final[str] = (
    "When you arrive at an insight, do you want me to test it or take it\n"
    "as you offered it?\n"
    "\n"
    "  [1] Take it as offered\n"
    "  [2] Test it lightly\n"
    '  [s] Skip — use the default ("Take it as offered")\n'
    "\n"
    "> "
)


# ---------------------------------------------------------------------------
# §3.5 Step 4 — Wish
# ---------------------------------------------------------------------------

STEP_WISH_PROMPT: Final[str] = (
    "One last thing. In a sentence — what are you hoping a second brain\nhelps you do?\n\n> "
)


# ---------------------------------------------------------------------------
# §3.6 Step 5 — Letter
# ---------------------------------------------------------------------------

LETTER_PRECALL: Final[str] = "One moment — writing back what I heard.\n"

LETTER_LEAD_IN: Final[str] = (
    "I read back to you what I picked up just now —\n"
    "\n"
    "{indented_paragraph}\n"
    "\n"
    "That paragraph is the first line of your about-me.md. You can edit\n"
    "it any time; I'll re-read it when we talk.\n"
)


# ---------------------------------------------------------------------------
# §3.7 Step 6 — Outro (pluralize-aware)
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
# Interrupt + error messages
# ---------------------------------------------------------------------------

PRE_COMMIT_INTERRUPT_MESSAGE: Final[str] = (
    "Stopped. No files written. Run remory init when you're ready.\n"
)

DURING_COMMIT_INTERRUPT_MESSAGE: Final[str] = (
    "Stopped mid-write. Some files may exist. Run remory doctor to inspect.\n"
)

THREE_STRIKES_MESSAGE: Final[str] = (
    "Three tries — let's stop here. Run remory init again when you're ready.\n"
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
# Re-prompt reasons + invalid handler — formatted by _steps to be friendly.
# ---------------------------------------------------------------------------

# When a re-prompt fires we just print the reason on its own line, then the
# question text resumes naturally on the next iteration. The wizard's tone
# is conversational; the validators carry the locked reason strings.
