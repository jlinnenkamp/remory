"""Pure validators for wizard prompt input.

Each ``validate_*`` returns either a parsed value (or :data:`SKIPPED`
sentinel) or a :class:`ValidationFailure` with a human-readable
``reason``. The wizard layer (``_steps`` and ``_orchestrator``) wraps
these in a re-prompt loop with a per-question 3-strikes counter.

The validators do not perform I/O. They take the raw line from
:func:`remory.ui.prompt_line` (no ``.strip()``) so they can detect
embedded newlines and reject them with a tone-appropriate reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "SKIPPED",
    "Skipped",
    "ValidationFailure",
    "ValidationResult",
    "validate_choice_with_skip",
    "validate_name",
    "validate_topic_picks",
    "validate_wish",
]


class Skipped:
    """Sentinel for "the user skipped this question".

    Distinct from a falsy value so callers can match the type rather
    than test for emptiness.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<SKIPPED>"


SKIPPED: Final[Skipped] = Skipped()


@dataclass(frozen=True)
class ValidationFailure:
    """A validator rejected the input. ``reason`` is the user-facing message."""

    reason: str


# ``ValidationResult`` is the return type for every validator; concrete
# validators narrow the success branch to their own value type.
ValidationResult = ValidationFailure  # placeholder; widened per-validator below


# ---------------------------------------------------------------------------
# Step 1 — name
# ---------------------------------------------------------------------------

# Locked re-prompt reasons (consolidated plan §3.2 + plan-tone matched).
_NAME_REASON_BLANK = "That came back blank — try again, or press Ctrl+C to bail."
_NAME_REASON_TOO_LONG = "A bit long — let's keep it under 60 characters so I can fit it on a line."
_NAME_REASON_NEWLINE = "Single line, no line breaks, please."

_SKIP_LITERAL = "[skip]"


def validate_name(raw: str) -> str | Skipped | ValidationFailure:
    """Validate the Step 1 name input.

    Accepts:
    - 1 to 60 characters after ``.strip()``.
    - The literal token ``[skip]`` (case-sensitive, brackets included)
      returns :data:`SKIPPED`.

    Rejects:
    - Empty / whitespace-only.
    - Embedded newlines.
    - >60 characters after ``.strip()``.

    Bare-word ``skip`` / ``s`` are accepted as names — some real users
    are named Skip. The bracketed ``[skip]`` form is the only skip path
    for free-text questions.
    """
    if "\n" in raw or "\r" in raw:
        return ValidationFailure(_NAME_REASON_NEWLINE)
    stripped = raw.strip()
    if stripped == _SKIP_LITERAL:
        return SKIPPED
    if not stripped:
        return ValidationFailure(_NAME_REASON_BLANK)
    if len(stripped) > 60:
        return ValidationFailure(_NAME_REASON_TOO_LONG)
    return stripped


# ---------------------------------------------------------------------------
# Step 2 — pick topics
# ---------------------------------------------------------------------------


_TOPIC_PICK_REASON_PARSE = (
    'That didn\'t parse — try something like "1,3" or just press Enter for all three.'
)
# Plan §3.3 has a single re-prompt for both parse failures and out-of-range
# integers. Keep the constant name reflecting both responsibilities.


def validate_topic_picks(
    raw: str,
    *,
    topic_names_lex: tuple[str, str, str],
) -> list[str] | ValidationFailure:
    """Parse the Step 2 multi-select input.

    Args:
        raw: the raw line from ``prompt_line``.
        topic_names_lex: the three built-in topic names in lexicographic
            order (``(coaching, job-profile, workout)`` for the
            built-ins).

    Returns:
        A list of topic names in **selection order** — the order the
        user typed them. Empty input → all three in lex order.
    """
    if "\n" in raw or "\r" in raw:
        return ValidationFailure(_TOPIC_PICK_REASON_PARSE)
    stripped = raw.strip()
    if not stripped:
        return list(topic_names_lex)
    # Split on commas and whitespace; allow either separator per §3.3.
    tokens = stripped.replace(",", " ").split()
    if not tokens:
        return ValidationFailure(_TOPIC_PICK_REASON_PARSE)
    seen: set[int] = set()
    out: list[str] = []
    for tok in tokens:
        # Reject multi-character integers like "01"; allow exactly "1"/"2"/"3".
        if tok not in {"1", "2", "3"}:
            # Provide the same parse hint regardless of branch — the user
            # doesn't need to discriminate "0" from "abc" from "01".
            return ValidationFailure(_TOPIC_PICK_REASON_PARSE)
        idx = int(tok)
        if idx in seen:
            # Duplicates collapse to a single pick; keep first occurrence.
            continue
        seen.add(idx)
        out.append(topic_names_lex[idx - 1])
    if not out:
        return ValidationFailure(_TOPIC_PICK_REASON_PARSE)
    return out


# ---------------------------------------------------------------------------
# Step 3.x — option-style choices with skip
# ---------------------------------------------------------------------------


_CHOICE_REASON = "Sorry, I didn't follow — pick 1, 2, or s."
_SKIP_TOKENS_LOWER: Final[frozenset[str]] = frozenset({"s", "skip"})


def validate_choice_with_skip(raw: str) -> str | Skipped | ValidationFailure:
    """Validate a Step 3.x option question.

    Accepted:
    - Exactly ``"1"`` or ``"2"`` (returned as the string).
    - ``"s"``, ``"S"``, ``"skip"``, ``"Skip"`` (case-insensitive) →
      :data:`SKIPPED`.

    Rejected:
    - Anything else: ``"0"``, ``"3"``+, ``"01"``, alphabetic input that
      isn't ``s``/``skip``, multi-line paste.
    """
    if "\n" in raw or "\r" in raw:
        return ValidationFailure(_CHOICE_REASON)
    stripped = raw.strip()
    if stripped in {"1", "2"}:
        return stripped
    if stripped.lower() in _SKIP_TOKENS_LOWER:
        return SKIPPED
    return ValidationFailure(_CHOICE_REASON)


# ---------------------------------------------------------------------------
# Step 4 — wish
# ---------------------------------------------------------------------------


_WISH_REASON_BLANK = "Take a guess — even a half-sentence helps me get the tone right."
_WISH_REASON_TOO_LONG = "Trim it a little — under 500 characters keeps it sentence-shaped."
_WISH_REASON_NEWLINE = "Single sentence, no line breaks, please."


def validate_wish(raw: str) -> str | Skipped | ValidationFailure:
    """Validate the Step 4 wish input.

    Accepts:
    - 1 to 500 characters after ``.strip()``.
    - The literal token ``[skip]`` (case-sensitive) returns :data:`SKIPPED`.

    Rejects:
    - Embedded newlines.
    - Empty (after strip).
    - >500 characters.
    """
    if "\n" in raw or "\r" in raw:
        return ValidationFailure(_WISH_REASON_NEWLINE)
    stripped = raw.strip()
    if stripped == _SKIP_LITERAL:
        return SKIPPED
    if not stripped:
        return ValidationFailure(_WISH_REASON_BLANK)
    if len(stripped) > 500:
        return ValidationFailure(_WISH_REASON_TOO_LONG)
    return stripped
