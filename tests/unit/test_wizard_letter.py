"""Letter-step tests (Phase 5, consolidated plan §11.3).

Pins: D1 BackendError fallback (5 subclasses + base + empty/whitespace
text), D4 byte-pinned fallback paragraph, D4 WARNING log shape (omit
``stderr_tail`` per the plan refinement), R3 prompt shape (5 named
tests).
"""

from __future__ import annotations

import logging

import pytest

from remory.backends.base import (
    BackendAuthError,
    BackendError,
    BackendInvocationError,
    BackendNotFoundError,
    BackendOutputError,
    BackendTimeoutError,
)
from remory.wizard._letter import (
    WizardAnswersForLetter,
    _compose_letter_prompt,
    compose_fallback_letter,
    compose_letter,
)
from tests.fakes.fake_backend import FakeBackend

# Canned answers for the fallback byte-pin (D4).
CANNED_ANSWERS = WizardAnswersForLetter(
    name="Sam",
    chosen_topics=("workout",),
    knobs_by_topic={"workout": {"tone": "warm", "strictness": "balanced"}},
    wish="stop forgetting what I told it",
)

# D4 byte-pinned expected fallback paragraph (verbatim).
EXPECTED_PARAGRAPH = (
    "(I couldn't reach the model just now, so this is a quick stand-in.) "
    "Hi Sam. You picked workout. "
    'You said: "stop forgetting what I told it". '
    "I'll keep what you bring me here, and only what you bring me."
)


# ---------------------------------------------------------------------------
# Backend success path
# ---------------------------------------------------------------------------


def test_compose_letter_returns_model_text_stripped_on_backend_success() -> None:
    backend = FakeBackend.with_letter_text("  one warm paragraph from the model.  \n")
    result = compose_letter(CANNED_ANSWERS, backend=backend)
    assert result == "one warm paragraph from the model."


# ---------------------------------------------------------------------------
# Backend-error fallback paths (D1: 5 subclasses + base + empty/whitespace)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_class",
    [
        BackendTimeoutError,
        BackendInvocationError,
        BackendOutputError,
        BackendAuthError,
        BackendNotFoundError,
    ],
)
def test_compose_letter_falls_back_when_backend_raises_each_subclass(
    exc_class: type[BackendError],
) -> None:
    """One test parametrised over the 5 named subclasses for §11.3."""
    backend = FakeBackend.with_letter_failure(exc_class)
    result = compose_letter(CANNED_ANSWERS, backend=backend)
    assert result == EXPECTED_PARAGRAPH


# Individual named tests for granularity (matches §11.3 names).
def test_compose_letter_falls_back_when_backend_raises_timeout() -> None:
    backend = FakeBackend.with_letter_failure(BackendTimeoutError)
    assert compose_letter(CANNED_ANSWERS, backend=backend) == EXPECTED_PARAGRAPH


def test_compose_letter_falls_back_when_backend_raises_invocation_error() -> None:
    backend = FakeBackend.with_letter_failure(
        BackendInvocationError, exit_code=1, stderr_tail="something"
    )
    assert compose_letter(CANNED_ANSWERS, backend=backend) == EXPECTED_PARAGRAPH


def test_compose_letter_falls_back_when_backend_raises_output_error() -> None:
    backend = FakeBackend.with_letter_failure(BackendOutputError)
    assert compose_letter(CANNED_ANSWERS, backend=backend) == EXPECTED_PARAGRAPH


def test_compose_letter_falls_back_when_backend_raises_auth_error() -> None:
    backend = FakeBackend.with_letter_failure(BackendAuthError)
    assert compose_letter(CANNED_ANSWERS, backend=backend) == EXPECTED_PARAGRAPH


def test_compose_letter_falls_back_when_backend_raises_not_found_error() -> None:
    backend = FakeBackend.with_letter_failure(BackendNotFoundError)
    assert compose_letter(CANNED_ANSWERS, backend=backend) == EXPECTED_PARAGRAPH


def test_compose_letter_falls_back_when_model_returns_empty_text() -> None:
    backend = FakeBackend.with_letter_text("")
    assert compose_letter(CANNED_ANSWERS, backend=backend) == EXPECTED_PARAGRAPH


def test_compose_letter_falls_back_when_model_returns_whitespace_only_text() -> None:
    backend = FakeBackend.with_letter_text("   \n\t  ")
    assert compose_letter(CANNED_ANSWERS, backend=backend) == EXPECTED_PARAGRAPH


# ---------------------------------------------------------------------------
# Fallback byte-pin (D4)
# ---------------------------------------------------------------------------


def test_compose_fallback_letter_pins_paragraph_for_canned_answers() -> None:
    """D4 byte-pin: the fallback paragraph must equal the literal in §1 D4."""
    paragraph = compose_fallback_letter(CANNED_ANSWERS)
    assert paragraph == EXPECTED_PARAGRAPH


def test_compose_fallback_letter_omits_name_clause_when_name_unset() -> None:
    answers = WizardAnswersForLetter(
        name=None,
        chosen_topics=("workout",),
        knobs_by_topic={"workout": {"tone": "warm", "strictness": "balanced"}},
        wish="stop forgetting",
    )
    paragraph = compose_fallback_letter(answers)
    assert "Hi" not in paragraph
    assert paragraph.startswith("(I couldn't reach the model just now,")
    assert "You picked workout. " in paragraph


def test_compose_fallback_letter_omits_wish_clause_when_wish_unset() -> None:
    answers = WizardAnswersForLetter(
        name="Sam",
        chosen_topics=("workout",),
        knobs_by_topic={"workout": {"tone": "warm", "strictness": "balanced"}},
        wish=None,
    )
    paragraph = compose_fallback_letter(answers)
    assert 'You said: "' not in paragraph
    assert "Hi Sam. " in paragraph


def test_compose_fallback_letter_uses_oxford_comma_for_three_topics() -> None:
    answers = WizardAnswersForLetter(
        name="Sam",
        chosen_topics=("coaching", "job-profile", "workout"),
        knobs_by_topic={},
        wish=None,
    )
    paragraph = compose_fallback_letter(answers)
    # Oxford comma per the plan.
    assert "You picked coaching, job-profile, and workout." in paragraph


# ---------------------------------------------------------------------------
# WARNING-log shape (D4 — omit stderr_tail)
# ---------------------------------------------------------------------------


def test_compose_letter_logs_warning_with_exception_type_and_wizard_step_extras_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend = FakeBackend.with_letter_failure(
        BackendInvocationError,
        exit_code=1,
        stderr_tail="something prompt-adjacent we must not log",
    )
    with caplog.at_level(logging.WARNING, logger="remory.wizard.letter"):
        compose_letter(CANNED_ANSWERS, backend=backend)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    rec = warnings[0]
    # D4: the structured extras include exception_type + wizard_step.
    assert rec.__dict__.get("exception_type") == "BackendInvocationError"
    assert rec.__dict__.get("wizard_step") == "letter"
    # D4 default-omit: the stderr_tail must not be on the log record.
    assert not hasattr(rec, "stderr_tail")
    # And nothing prompt-adjacent leaked into the message itself.
    assert "prompt-adjacent" not in rec.getMessage()


def test_compose_letter_logs_warning_for_empty_text_with_empty_model_output_extra(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend = FakeBackend.with_letter_text("   ")
    with caplog.at_level(logging.WARNING, logger="remory.wizard.letter"):
        compose_letter(CANNED_ANSWERS, backend=backend)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].__dict__.get("exception_type") == "empty_model_output"


# ---------------------------------------------------------------------------
# R3 — _compose_letter_prompt shape (5 named tests)
# ---------------------------------------------------------------------------


def test_compose_letter_prompt_includes_name_topics_wish_for_set_answers() -> None:
    prompt = _compose_letter_prompt(CANNED_ANSWERS)
    assert "Name: Sam" in prompt
    assert "Topics chosen: workout" in prompt
    assert "What they're hoping for: stop forgetting what I told it" in prompt


def test_compose_letter_prompt_omits_unset_name_section() -> None:
    answers = WizardAnswersForLetter(
        name=None,
        chosen_topics=("workout",),
        knobs_by_topic={"workout": {"tone": "warm", "strictness": "balanced"}},
        wish="stop forgetting",
    )
    prompt = _compose_letter_prompt(answers)
    assert "Name:" not in prompt


def test_compose_letter_prompt_omits_unset_wish_section() -> None:
    answers = WizardAnswersForLetter(
        name="Sam",
        chosen_topics=("workout",),
        knobs_by_topic={"workout": {"tone": "warm", "strictness": "balanced"}},
        wish=None,
    )
    prompt = _compose_letter_prompt(answers)
    assert "What they're hoping for:" not in prompt


def test_compose_letter_prompt_renders_knobs_per_topic_in_selection_order() -> None:
    answers = WizardAnswersForLetter(
        name="Sam",
        chosen_topics=("workout", "job-profile"),
        knobs_by_topic={
            "workout": {"tone": "direct", "strictness": "rigorous"},
            "job-profile": {"tone": "warm", "strictness": "gentle"},
        },
        wish=None,
    )
    prompt = _compose_letter_prompt(answers)
    assert "Topics chosen: workout, job-profile" in prompt
    workout_idx = prompt.index("workout: tone=direct, strictness=rigorous")
    jp_idx = prompt.index("job-profile: tone=warm, strictness=gentle")
    # Selection order: workout came first.
    assert workout_idx < jp_idx


def test_compose_letter_prompt_ends_with_pinned_output_format_request() -> None:
    prompt = _compose_letter_prompt(CANNED_ANSWERS)
    assert prompt.rstrip("\n").endswith(
        "Respond with one paragraph. No preamble, no headings, second person, 3 to 5 sentences."
    )
