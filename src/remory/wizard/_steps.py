"""Pure-I/O step functions for the Phase 5 wizard.

Each step prints its prompt(s) via :func:`remory.ui.prompt_line`'s test
seam and returns a parsed value (or :data:`SKIPPED` when the user
skipped). Three-strikes is enforced per-question by
:func:`_prompt_with_validator`; the counter resets at the start of
each call.

Step functions do NOT import :mod:`remory.backends`. The letter step
lives in the orchestrator (it is the only one that calls the model).
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from remory.schema import Schema, load_builtin
from remory.ui import prompt_line
from remory.wizard import _strings as S
from remory.wizard._validators import (
    Skipped,
    ValidationFailure,
    validate_choice_with_skip,
    validate_name,
    validate_topic_picks,
    validate_wish,
)

__all__ = [
    "TOPIC_NAMES_LEX",
    "WizardThreeStrikesError",
    "step_letter_lead_in",
    "step_letter_precall",
    "step_name",
    "step_outro",
    "step_pick_topics",
    "step_topic_q1",
    "step_topic_q2",
    "step_welcome",
    "step_wish",
]


# Built-in topic names in lexicographic order — fixed for v0.1.
TOPIC_NAMES_LEX: tuple[str, str, str] = ("coaching", "job-profile", "workout")


class WizardThreeStrikesError(Exception):
    """The user submitted three consecutive invalid attempts on a single prompt.

    Per consolidated plan §2 #5 the counter is per-question, not
    wizard-global. CLI maps to exit 2 with the locked
    "Three tries — let's stop here." message.
    """


def _prompt_with_validator[T](
    prompt: str,
    validator: Callable[[str], T | Skipped | ValidationFailure],
    *,
    console: Console,
    input_fn: object | None,
) -> T | Skipped:
    """Run a re-prompt loop with a per-question 3-strikes counter.

    Args:
        prompt: the prompt text (printed once, then again on each
            re-prompt).
        validator: a callable that accepts the raw line and returns
            either a parsed value, :data:`SKIPPED`, or a
            :class:`ValidationFailure`.

    Raises:
        WizardThreeStrikesError: after three consecutive invalid
            attempts. The counter is per-call (per-question).
    """
    attempts = 0
    while True:
        raw = prompt_line(prompt, console=console, input_fn=input_fn)
        result = validator(raw)
        if isinstance(result, ValidationFailure):
            attempts += 1
            if attempts >= 3:
                raise WizardThreeStrikesError(
                    f"three consecutive invalid attempts on prompt: {prompt!r}"
                )
            console.out(result.reason)
            continue
        return result


# ---------------------------------------------------------------------------
# Welcome / outro / letter UI (no input — pure print)
# ---------------------------------------------------------------------------


def step_welcome(*, console: Console, data_dir_str: str) -> None:
    """Print the §3.1 welcome banner with the data dir inlined per D3."""
    console.out(S.WELCOME_TEMPLATE.format(data_dir=data_dir_str))


def step_letter_precall(*, console: Console) -> None:
    """Print the pre-call line per §3.6."""
    console.out(S.LETTER_PRECALL)


def step_letter_lead_in(*, console: Console, paragraph: str) -> None:
    """Print the §3.6 lead-in plus the model paragraph indented two spaces."""
    indented = "\n".join(f"  {line}" for line in paragraph.splitlines() or [""])
    console.out(S.LETTER_LEAD_IN.format(indented_paragraph=indented))


def step_outro(
    *,
    console: Console,
    data_dir_str: str,
    chosen_topics: list[str],
    about_me_path_str: str,
) -> None:
    """Print the §3.7 outro, pluralize-aware per D9."""
    if len(chosen_topics) == 1:
        console.out(
            S.OUTRO_SINGULAR_TEMPLATE.format(
                data_dir=data_dir_str,
                topic=chosen_topics[0],
                about_me_path=about_me_path_str,
            )
        )
        return
    second = chosen_topics[1]
    console.out(
        S.OUTRO_PLURAL_TEMPLATE.format(
            data_dir=data_dir_str,
            topics_csv=", ".join(chosen_topics),
            about_me_path=about_me_path_str,
            first_topic=chosen_topics[0],
            second_topic=second,
        )
    )


# ---------------------------------------------------------------------------
# Step 1 — Name
# ---------------------------------------------------------------------------


def step_name(
    *,
    console: Console,
    input_fn: object | None,
) -> str | None:
    """Run the §3.2 name prompt with the validator + 3-strikes counter.

    Returns the validated name, or ``None`` if the user skipped via
    ``[skip]``.
    """
    result = _prompt_with_validator(
        S.STEP_NAME_PROMPT,
        validate_name,
        console=console,
        input_fn=input_fn,
    )
    if isinstance(result, Skipped):
        return None
    return result


# ---------------------------------------------------------------------------
# Step 2 — Pick topics
# ---------------------------------------------------------------------------


def step_pick_topics(
    *,
    console: Console,
    input_fn: object | None,
) -> list[str]:
    """Run the §3.3 multi-select prompt. Returns the topic names in selection order."""

    def _bound_validator(raw: str) -> list[str] | ValidationFailure:
        return validate_topic_picks(raw, topic_names_lex=TOPIC_NAMES_LEX)

    result = _prompt_with_validator(
        S.PICK_TOPICS_PROMPT,
        _bound_validator,
        console=console,
        input_fn=input_fn,
    )
    if isinstance(result, Skipped):  # validate_topic_picks never returns SKIPPED
        # Defensive: empty input returns the all-three default; we
        # never reach this branch in practice. List it explicitly so
        # the type narrows for the caller.
        return list(TOPIC_NAMES_LEX)
    return result


# ---------------------------------------------------------------------------
# Step 3 — Per-topic preamble + Q1 + Q2
# ---------------------------------------------------------------------------


# Map topic name → (preamble, q1_prompt, q2_prompt). Each Q maps the
# digit choice ("1" / "2") to the schema's option *value* (warm/direct,
# gentle/rigorous, etc.). The mapping below is the binding §3.4 contract.
#
# v0.2 forward-debt: derive the digit→value mapping from the loaded
# schema (`schema.wizard_questions[*].options[*].value`) instead of
# hand-maintaining it here. If a schema YAML adds/reorders options the
# current shape silently drifts; the load-time derivation closes that.
_PER_TOPIC: dict[str, tuple[str, str, str, dict[str, str], dict[str, str]]] = {
    "job-profile": (
        S.JOB_PROFILE_PREAMBLE,
        S.JOB_PROFILE_Q1,
        S.JOB_PROFILE_Q2,
        {"1": "warm", "2": "direct"},
        {"1": "gentle", "2": "rigorous"},
    ),
    "workout": (
        S.WORKOUT_PREAMBLE,
        S.WORKOUT_Q1,
        S.WORKOUT_Q2,
        {"1": "warm", "2": "direct"},
        {"1": "gentle", "2": "rigorous"},
    ),
    "coaching": (
        S.COACHING_PREAMBLE,
        S.COACHING_Q1,
        S.COACHING_Q2,
        {"1": "warm", "2": "balanced"},
        {"1": "gentle", "2": "balanced"},
    ),
}


def _print_topic_preamble(topic: str, *, console: Console) -> None:
    preamble, _, _, _, _ = _PER_TOPIC[topic]
    console.out(preamble)


def _resolve_choice(
    topic: str,
    knob_id: str,
    digit_or_skip: str | Skipped,
    schema: Schema,
) -> str:
    """Resolve a digit choice (or SKIPPED) to a schema value.

    For SKIPPED, returns the schema's default for the matching knob.
    """
    if isinstance(digit_or_skip, Skipped):
        if knob_id == "tone":
            return schema.defaults.tone
        return schema.defaults.strictness
    _, _, _, q1_map, q2_map = _PER_TOPIC[topic]
    mapping = q1_map if knob_id == "tone" else q2_map
    return mapping[digit_or_skip]


def step_topic_q1(
    topic: str,
    *,
    console: Console,
    input_fn: object | None,
) -> str:
    """Run the §3.4 Q1 (tone) for ``topic``. Returns the schema value."""
    _print_topic_preamble(topic, console=console)
    _, q1_prompt, _, _, _ = _PER_TOPIC[topic]
    raw_choice = _prompt_with_validator(
        q1_prompt,
        validate_choice_with_skip,
        console=console,
        input_fn=input_fn,
    )
    schema = load_builtin(topic)
    return _resolve_choice(topic, "tone", raw_choice, schema)


def step_topic_q2(
    topic: str,
    *,
    console: Console,
    input_fn: object | None,
) -> str:
    """Run the §3.4 Q2 (strictness) for ``topic``. Returns the schema value."""
    _, _, q2_prompt, _, _ = _PER_TOPIC[topic]
    raw_choice = _prompt_with_validator(
        q2_prompt,
        validate_choice_with_skip,
        console=console,
        input_fn=input_fn,
    )
    schema = load_builtin(topic)
    return _resolve_choice(topic, "strictness", raw_choice, schema)


# ---------------------------------------------------------------------------
# Step 4 — Wish
# ---------------------------------------------------------------------------


def step_wish(
    *,
    console: Console,
    input_fn: object | None,
) -> str | None:
    """Run the §3.5 wish prompt. Returns the wish, or ``None`` on skip."""
    result = _prompt_with_validator(
        S.STEP_WISH_PROMPT,
        validate_wish,
        console=console,
        input_fn=input_fn,
    )
    if isinstance(result, Skipped):
        return None
    return result
