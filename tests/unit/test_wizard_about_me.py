"""about-me.md byte-format tests (Phase 5 §6 byte format preserved in Phase 6).

Pins: §6 byte format (paragraph + blank + ``---`` + 3 facts), blank
fields after colons when name/wish are unset, selection-order topics.

Phase 6 promoted :class:`WizardAnswers` from a mutable dataclass to a
frozen Pydantic model; the format produced by ``_about_me_bytes`` is
unchanged.
"""

from __future__ import annotations

from remory.wizard import WizardAnswers, WizardKnobs
from remory.wizard._commit import _about_me_bytes

LETTER = (
    'Hi Sam. You picked workout. You said: "x". I\'ll keep what you bring me here, '
    "and only what you bring me."
)


def test_about_me_bytes_pins_format_with_letter_name_topics_wish_set() -> None:
    answers = WizardAnswers(
        version=1,
        name="Sam",
        chosen_topics=("workout",),
        knobs_by_topic={"workout": WizardKnobs(tone="warm", strictness="balanced")},
        wish="stop forgetting",
    )
    out = _about_me_bytes(answers, LETTER)
    expected = f"{LETTER}\n\n---\nname: Sam\ntopics: workout\nwish: stop forgetting\n"
    assert out == expected


def test_about_me_bytes_renders_blank_after_colon_for_omitted_name() -> None:
    answers = WizardAnswers(
        version=1,
        name=None,
        chosen_topics=("workout",),
        knobs_by_topic={"workout": WizardKnobs(tone="warm", strictness="balanced")},
        wish="stop forgetting",
    )
    out = _about_me_bytes(answers, LETTER)
    assert "name: \n" in out
    assert "wish: stop forgetting\n" in out


def test_about_me_bytes_renders_blank_after_colon_for_omitted_wish() -> None:
    answers = WizardAnswers(
        version=1,
        name="Sam",
        chosen_topics=("workout",),
        knobs_by_topic={"workout": WizardKnobs(tone="warm", strictness="balanced")},
        wish=None,
    )
    out = _about_me_bytes(answers, LETTER)
    assert "name: Sam\n" in out
    assert "wish: \n" in out


def test_about_me_bytes_orders_topics_in_selection_order_not_lex() -> None:
    """Selection-order topics in the facts block (matches §3.7 outro)."""
    answers = WizardAnswers(
        version=1,
        name="Sam",
        chosen_topics=("workout", "coaching", "job-profile"),
        knobs_by_topic={},
        wish=None,
    )
    out = _about_me_bytes(answers, LETTER)
    assert "topics: workout, coaching, job-profile\n" in out
    # Lex order would be 'coaching, job-profile, workout' — must NOT appear.
    assert "topics: coaching, job-profile, workout\n" not in out
