"""Compose the wizard's "letter" paragraph.

Two paths:

- ``compose_letter(answers, *, backend)`` — the happy path. Builds the
  prompt, calls ``backend.headless()``, returns the model's text. On any
  ``BackendError`` (or empty/whitespace text), degrades to
  :func:`compose_fallback_letter` and emits a single WARNING log per D1
  + D4.

- :func:`compose_fallback_letter` — pure, no I/O. Composes a stand-in
  paragraph from the answers using the byte-pinned
  :data:`_FALLBACK_TEMPLATE`.

D2: ``backend.headless`` is called with ``agent=None``; the
``wizard.md`` Claude Code subagent lands in Phase 6.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from remory.backends.base import BackendError

if TYPE_CHECKING:
    from remory.backends.base import Backend

__all__ = [
    "WizardAnswersForLetter",
    "compose_fallback_letter",
    "compose_letter",
]

_log = logging.getLogger("remory.wizard.letter")


# ---------------------------------------------------------------------------
# Minimal answer shape (decoupled from the Phase 4 dataclass for testability)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WizardAnswersForLetter:
    """The fields :func:`compose_letter` needs.

    Decoupled from ``WizardAnswers`` (the orchestrator's dataclass) so
    the letter unit tests can pin behaviour without depending on the
    full Phase 5 surface. The orchestrator builds one of these from a
    ``WizardAnswers``.
    """

    name: str | None
    chosen_topics: tuple[str, ...]
    knobs_by_topic: dict[str, dict[str, str]]
    wish: str | None


# ---------------------------------------------------------------------------
# Prompt composition (R3-pinned shape; tested by 5 named tests)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are the Remory wizard. The user just finished a short setup interview.\n"
    "Read what they shared back to them as one warm paragraph in second person,\n"
    "3 to 5 sentences, no preamble, no headings, no bullet points. Do not\n"
    "restate the topic descriptions; reflect what *this* user said. End on a\n"
    "note that signals you'll keep what they bring you."
)

_OUTPUT_FORMAT_REQUEST = (
    "Respond with one paragraph. No preamble, no headings, second person, 3 to 5 sentences."
)


def _compose_letter_prompt(answers: WizardAnswersForLetter) -> str:
    """Build the user prompt for the letter LLM call.

    Sections (in order, omitted entirely when the source field is unset):

    1. ``Name: <name>``
    2. ``Topics chosen: <comma-separated, selection order>``
    3. ``Knobs per topic:`` block (per topic, selection order:
       ``  <topic>: tone=<tone>, strictness=<strictness>``).
    4. ``What they're hoping for: <wish>``

    System prompt is prepended via two newlines; output-format request
    is appended as the final line.
    """
    parts: list[str] = [_SYSTEM_PROMPT, ""]
    if answers.name is not None:
        parts.append(f"Name: {answers.name}")
    if answers.chosen_topics:
        parts.append(f"Topics chosen: {', '.join(answers.chosen_topics)}")
        parts.append("Knobs per topic:")
        for topic in answers.chosen_topics:
            knobs = answers.knobs_by_topic.get(topic, {})
            tone = knobs.get("tone", "")
            strictness = knobs.get("strictness", "")
            parts.append(f"  {topic}: tone={tone}, strictness={strictness}")
    if answers.wish is not None:
        parts.append(f"What they're hoping for: {answers.wish}")
    parts.append("")
    parts.append(_OUTPUT_FORMAT_REQUEST)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Fallback paragraph (D4 byte-pinned)
# ---------------------------------------------------------------------------

# D4: byte-pinned. Tested by
# ``test_compose_fallback_letter_pins_paragraph_for_canned_answers``.
# Do NOT modify without updating the consolidated plan §1 D4.
_FALLBACK_TEMPLATE = (
    "(I couldn't reach the model just now, so this is a quick stand-in.) "
    "{name_clause}You picked {topics_clause}. {wish_clause}"
    "I'll keep what you bring me here, and only what you bring me."
)


def _join_topics(topics: tuple[str, ...]) -> str:
    """Human-readable join: 1 → bare; 2 → 'X and Y'; 3+ → Oxford."""
    if len(topics) == 1:
        return topics[0]
    if len(topics) == 2:
        return f"{topics[0]} and {topics[1]}"
    head = ", ".join(topics[:-1])
    return f"{head}, and {topics[-1]}"


def compose_fallback_letter(answers: WizardAnswersForLetter) -> str:
    """Compose the hand-written fallback paragraph.

    Pure: no I/O. Byte-stable for canned inputs (D4).
    """
    name_clause = f"Hi {answers.name}. " if answers.name else ""
    topics_clause = _join_topics(answers.chosen_topics)
    wish_clause = f'You said: "{answers.wish}". ' if answers.wish else ""
    return _FALLBACK_TEMPLATE.format(
        name_clause=name_clause,
        topics_clause=topics_clause,
        wish_clause=wish_clause,
    )


# ---------------------------------------------------------------------------
# compose_letter — happy path + fallback
# ---------------------------------------------------------------------------


def compose_letter(
    answers: WizardAnswersForLetter,
    *,
    backend: Backend,
    timeout_seconds: int = 30,
) -> str:
    """Generate the wizard's letter paragraph.

    Calls ``backend.headless()`` once with the composed prompt. On
    success, returns the model's ``text.strip()``. On any
    :class:`BackendError` (D1) or empty/whitespace text, falls back to
    :func:`compose_fallback_letter`, logs a single WARNING with
    ``exception_type`` + ``wizard_step`` extras (D4 — no
    ``stderr_tail``).
    """
    prompt = _compose_letter_prompt(answers)
    try:
        result = backend.headless(
            prompt=prompt,
            agent=None,
            cwd=None,
            json_output=False,
            timeout_seconds=timeout_seconds,
        )
    except BackendError as exc:
        _log.warning(
            "wizard letter step degraded to fallback paragraph",
            extra={
                "exception_type": type(exc).__name__,
                "wizard_step": "letter",
            },
        )
        return compose_fallback_letter(answers)

    text = result.text.strip()
    if not text:
        _log.warning(
            "wizard letter step degraded to fallback paragraph",
            extra={
                "exception_type": "empty_model_output",
                "wizard_step": "letter",
            },
        )
        return compose_fallback_letter(answers)
    return text
