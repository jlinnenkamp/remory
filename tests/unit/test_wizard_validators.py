"""Pure-validator tests (Phase 5, consolidated plan §11.1).

The wizard's validators are pure functions over the raw input line.
These tests pin: accept paths, reject paths, the literal skip-token
shapes, and the human-facing reason strings (without depending on
their exact wording — the validators carry the locked text).
"""

from __future__ import annotations

from remory.wizard._validators import (
    SKIPPED,
    ValidationFailure,
    validate_choice_with_skip,
    validate_name,
    validate_topic_picks,
    validate_wish,
)

LEX_TOPICS = ("coaching", "job-profile", "workout")


# ---------------------------------------------------------------------------
# validate_name
# ---------------------------------------------------------------------------


def test_validate_name_returns_value_for_valid_input() -> None:
    assert validate_name("Sam") == "Sam"


def test_validate_name_rejects_empty_string_with_blank_reason() -> None:
    result = validate_name("")
    assert isinstance(result, ValidationFailure)
    assert "blank" in result.reason


def test_validate_name_rejects_over_60_chars_with_too_long_reason() -> None:
    result = validate_name("x" * 61)
    assert isinstance(result, ValidationFailure)
    assert "60" in result.reason


def test_validate_name_rejects_input_containing_newline_with_newline_reason() -> None:
    result = validate_name("Sam\n")
    assert isinstance(result, ValidationFailure)
    # Validator says "Single line, no line breaks, please."
    assert "line" in result.reason.lower()


def test_validate_name_accepts_literal_bracketed_skip_token_and_returns_skipped() -> None:
    assert validate_name("[skip]") is SKIPPED


def test_validate_name_accepts_bare_word_skip_as_value_because_only_bracketed_form_skips() -> None:
    """Step 1 is free-text; the only skip path is the literal bracketed
    ``[skip]`` token per consolidated plan §7. Bare-word ``skip`` is a
    valid name (some real users *are* named Skip).
    """
    result = validate_name("skip")
    assert result == "skip"


# ---------------------------------------------------------------------------
# validate_topic_picks
# ---------------------------------------------------------------------------


def test_validate_topic_picks_empty_returns_all_three_in_lex_order() -> None:
    result = validate_topic_picks("", topic_names_lex=LEX_TOPICS)
    assert result == list(LEX_TOPICS)


def test_validate_topic_picks_single_returns_one_topic() -> None:
    result = validate_topic_picks("2", topic_names_lex=LEX_TOPICS)
    assert result == ["job-profile"]


def test_validate_topic_picks_comma_separated_returns_selection_order() -> None:
    """Comma-separated picks preserve the order the user typed."""
    result = validate_topic_picks("3,1", topic_names_lex=LEX_TOPICS)
    assert result == ["workout", "coaching"]


def test_validate_topic_picks_space_separated_returns_selection_order() -> None:
    result = validate_topic_picks("2 3", topic_names_lex=LEX_TOPICS)
    assert result == ["job-profile", "workout"]


def test_validate_topic_picks_rejects_zero_with_parse_reason() -> None:
    result = validate_topic_picks("0", topic_names_lex=LEX_TOPICS)
    assert isinstance(result, ValidationFailure)


def test_validate_topic_picks_rejects_four_with_parse_reason() -> None:
    result = validate_topic_picks("4", topic_names_lex=LEX_TOPICS)
    assert isinstance(result, ValidationFailure)


def test_validate_topic_picks_rejects_alphabetic_with_parse_reason() -> None:
    result = validate_topic_picks("abc", topic_names_lex=LEX_TOPICS)
    assert isinstance(result, ValidationFailure)


def test_validate_topic_picks_rejects_multiline_paste_with_parse_reason() -> None:
    result = validate_topic_picks("1\n2", topic_names_lex=LEX_TOPICS)
    assert isinstance(result, ValidationFailure)


# ---------------------------------------------------------------------------
# validate_choice_with_skip
# ---------------------------------------------------------------------------


def test_validate_choice_with_skip_accepts_1_and_2_as_options() -> None:
    assert validate_choice_with_skip("1") == "1"
    assert validate_choice_with_skip("2") == "2"


def test_validate_choice_with_skip_accepts_s_skip_S_Skip_case_insensitive() -> None:
    assert validate_choice_with_skip("s") is SKIPPED
    assert validate_choice_with_skip("S") is SKIPPED
    assert validate_choice_with_skip("skip") is SKIPPED
    assert validate_choice_with_skip("Skip") is SKIPPED


def test_validate_choice_with_skip_rejects_zero_three_alpha_zerodigit_multiline() -> None:
    for raw in ("0", "3", "abc", "01", "1\n2"):
        result = validate_choice_with_skip(raw)
        assert isinstance(result, ValidationFailure), f"expected reject on {raw!r}"


# ---------------------------------------------------------------------------
# validate_wish
# ---------------------------------------------------------------------------


def test_validate_wish_returns_value_for_valid_input() -> None:
    assert validate_wish("stop forgetting things I said") == "stop forgetting things I said"


def test_validate_wish_rejects_empty_with_blank_reason() -> None:
    result = validate_wish("")
    assert isinstance(result, ValidationFailure)
    assert "guess" in result.reason or "blank" in result.reason.lower()


def test_validate_wish_rejects_over_500_with_too_long_reason() -> None:
    result = validate_wish("x" * 501)
    assert isinstance(result, ValidationFailure)
    assert "500" in result.reason


def test_validate_wish_rejects_newline_with_single_sentence_reason() -> None:
    result = validate_wish("hello\nworld")
    assert isinstance(result, ValidationFailure)
    # Validator says "Single sentence, no line breaks, please."
    assert "single sentence" in result.reason.lower()


def test_validate_wish_accepts_literal_bracketed_skip_token() -> None:
    assert validate_wish("[skip]") is SKIPPED
