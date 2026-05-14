"""Pydantic wire-format pins for :class:`WizardAnswers` (Phase 6 §6.1).

The model is the contract between the ``wizard.md`` subagent's
``answers.json`` and the harness. ``frozen=True`` + ``extra="forbid"``
mean adversarial or stray output surfaces as a validation error the
orchestrator can catch and trigger one repair round.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from remory.wizard import WizardAnswers, WizardKnobs


def test_wizard_answers_round_trips_through_json_when_well_formed() -> None:
    """Valid JSON round-trips: model_dump → model_validate yields equal model."""
    original = WizardAnswers(
        version=1,
        name="Sam",
        chosen_topics=("workout", "coaching"),
        knobs_by_topic={
            "workout": WizardKnobs(tone="warm", strictness="balanced"),
            "coaching": WizardKnobs(tone="balanced", strictness="gentle"),
        },
        wish="stop forgetting",
    )
    dumped = original.model_dump(mode="json")
    restored = WizardAnswers.model_validate(dumped)
    assert restored == original


def test_wizard_answers_rejects_unknown_tone_value() -> None:
    payload: dict[str, object] = {
        "version": 1,
        "name": "Sam",
        "chosen_topics": ["workout"],
        "knobs_by_topic": {"workout": {"tone": "chipper", "strictness": "balanced"}},
        "wish": None,
    }
    with pytest.raises(ValidationError):
        WizardAnswers.model_validate(payload)


def test_wizard_answers_rejects_unknown_strictness_value() -> None:
    payload: dict[str, object] = {
        "version": 1,
        "name": None,
        "chosen_topics": ["workout"],
        "knobs_by_topic": {"workout": {"tone": "warm", "strictness": "draconian"}},
        "wish": None,
    }
    with pytest.raises(ValidationError):
        WizardAnswers.model_validate(payload)


def test_wizard_answers_rejects_extra_top_level_key() -> None:
    """``extra="forbid"`` is load-bearing for the wire-format contract."""
    payload: dict[str, object] = {
        "version": 1,
        "name": "Sam",
        "chosen_topics": ["workout"],
        "knobs_by_topic": {},
        "wish": None,
        "unexpected": "leak",
    }
    with pytest.raises(ValidationError):
        WizardAnswers.model_validate(payload)


def test_wizard_answers_rejects_version_other_than_1() -> None:
    """``version`` is the forward-compat hook; the only valid value today is 1."""
    payload: dict[str, object] = {
        "version": 2,
        "name": None,
        "chosen_topics": [],
        "knobs_by_topic": {},
        "wish": None,
    }
    with pytest.raises(ValidationError):
        WizardAnswers.model_validate(payload)


def test_wizard_answers_allows_null_name_and_null_wish() -> None:
    """Null name + null wish (the user skipped both free-text questions) is valid."""
    payload: dict[str, object] = {
        "version": 1,
        "name": None,
        "chosen_topics": ["workout"],
        "knobs_by_topic": {"workout": {"tone": "direct", "strictness": "rigorous"}},
        "wish": None,
    }
    answers = WizardAnswers.model_validate(payload)
    assert answers.name is None
    assert answers.wish is None


def test_wizard_answers_rejects_knobs_for_unchosen_topic() -> None:
    """A WizardKnobs entry without a matching tone/strictness key fails.

    The harness does NOT cross-validate `knobs_by_topic` keys against
    `chosen_topics` at the Pydantic level (the spec intentionally allows
    spurious knobs to coexist with selected topics — the COMMIT step
    consults `chosen_topics` only). What we DO pin: an invalid
    WizardKnobs entry (missing or extra knob keys) is rejected.
    """
    payload: dict[str, object] = {
        "version": 1,
        "name": None,
        "chosen_topics": ["workout"],
        "knobs_by_topic": {"workout": {"tone": "warm"}},  # missing strictness
        "wish": None,
    }
    with pytest.raises(ValidationError):
        WizardAnswers.model_validate(payload)
