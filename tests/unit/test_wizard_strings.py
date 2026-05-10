"""Locked-string assertions for the wizard's user-facing text.

Coverage gap (a) from the Phase 5 review: `_strings.py` centralises the
binding Phase 4 §3 user-facing text, but no test pinned that the
constants carry the right content. A typo (e.g. R1 workout strictness
skip line) would land silently. Each test asserts substrings on the
``_strings`` constants directly, not inline-quoted copies — single
source of truth.

Coverage gap (b): pluralize-aware outro (D9) — singular vs plural
templates carry different ``topic:`` / ``topics:`` labels.
"""

from __future__ import annotations

import io

from rich.console import Console

from remory.wizard import _strings as S
from remory.wizard._steps import step_outro

# ---------------------------------------------------------------------------
# §3.1 Welcome (R3 path-inlined)
# ---------------------------------------------------------------------------


def test_welcome_template_opens_with_remory_dot() -> None:
    assert S.WELCOME_TEMPLATE.startswith("Remory.\n")


def test_welcome_template_inlines_data_dir_placeholder() -> None:
    """D3: the welcome banner inlines the resolved data_dir."""
    assert "Your data will live at: {data_dir}" in S.WELCOME_TEMPLATE


def test_welcome_template_promises_no_files_until_end() -> None:
    """The 'nothing is written until the very end' promise is load-bearing."""
    assert "Nothing is written until the very end" in S.WELCOME_TEMPLATE


# ---------------------------------------------------------------------------
# §3.2 Step 1 — Name
# ---------------------------------------------------------------------------


def test_step_name_prompt_pins_question_and_input_marker() -> None:
    assert "What should I call you?" in S.STEP_NAME_PROMPT
    assert S.STEP_NAME_PROMPT.endswith("> ")


# ---------------------------------------------------------------------------
# §3.3 Step 2 — Pick topics
# ---------------------------------------------------------------------------


def test_pick_topics_prompt_lists_three_built_ins_in_lex_order() -> None:
    assert "[1] job-profile" in S.PICK_TOPICS_PROMPT
    assert "[2] workout" in S.PICK_TOPICS_PROMPT
    assert "[3] coaching" in S.PICK_TOPICS_PROMPT


def test_pick_topics_prompt_explains_press_enter_means_all_three() -> None:
    assert "Press Enter for all three" in S.PICK_TOPICS_PROMPT


# ---------------------------------------------------------------------------
# §3.4.1 job-profile
# ---------------------------------------------------------------------------


def test_job_profile_preamble_describes_career_direction() -> None:
    assert S.JOB_PROFILE_PREAMBLE.startswith("job-profile — career direction.")


def test_job_profile_q1_pins_contradictory_phrasing_and_skip_default() -> None:
    assert "contradictory across sessions" in S.JOB_PROFILE_Q1
    assert 'Skip — use the default ("Gently flag, with care")' in S.JOB_PROFILE_Q1


def test_job_profile_q2_pins_rigorous_phrasing_and_skip_default() -> None:
    assert "How rigorous should I be" in S.JOB_PROFILE_Q2
    assert 'Skip — use the default ("Encouraging")' in S.JOB_PROFILE_Q2


# ---------------------------------------------------------------------------
# §3.4.2 workout — R1 wording on Q2
# ---------------------------------------------------------------------------


def test_workout_preamble_promises_not_to_program_for_user() -> None:
    assert "I won't program for you" in S.WORKOUT_PREAMBLE


def test_workout_q1_pins_session_goes_badly_phrasing_and_skip_default() -> None:
    assert "session goes badly" in S.WORKOUT_Q1
    assert 'Skip — use the default ("Direct; just tell me")' in S.WORKOUT_Q1


def test_workout_q2_pins_r1_leave_it_at_the_default_for_now_balanced() -> None:
    """R1: workout strictness skip wording is `leave it at the default for now`,
    not `use the default` — schema default `balanced` doesn't match an offered
    option, and the skip line acknowledges that without apologising.
    """
    assert 'Skip — leave it at the default for now ("balanced")' in S.WORKOUT_Q2


# ---------------------------------------------------------------------------
# §3.4.3 coaching
# ---------------------------------------------------------------------------


def test_coaching_preamble_promises_not_to_play_therapist() -> None:
    assert "I won't play therapist" in S.COACHING_PREAMBLE


def test_coaching_q1_pins_close_and_warm_skip_default() -> None:
    assert "close and warm" in S.COACHING_Q1
    assert 'Skip — use the default ("Close and warm")' in S.COACHING_Q1


def test_coaching_q2_pins_take_it_as_offered_skip_default() -> None:
    assert 'Skip — use the default ("Take it as offered")' in S.COACHING_Q2


# ---------------------------------------------------------------------------
# §3.5 Step 4 — Wish
# ---------------------------------------------------------------------------


def test_step_wish_prompt_asks_what_you_are_hoping() -> None:
    assert "what are you hoping" in S.STEP_WISH_PROMPT
    assert S.STEP_WISH_PROMPT.endswith("> ")


# ---------------------------------------------------------------------------
# §3.6 Step 5 — Letter
# ---------------------------------------------------------------------------


def test_letter_precall_pins_one_moment_phrasing() -> None:
    assert S.LETTER_PRECALL == "One moment — writing back what I heard.\n"


def test_letter_lead_in_frames_paragraph_and_about_me_promise() -> None:
    assert "I read back to you what I picked up just now —" in S.LETTER_LEAD_IN
    assert "{indented_paragraph}" in S.LETTER_LEAD_IN
    assert "That paragraph is the first line of your about-me.md" in S.LETTER_LEAD_IN


# ---------------------------------------------------------------------------
# §3.7 Step 6 — Outro (pluralize-aware per D9)
# ---------------------------------------------------------------------------


def test_outro_singular_uses_topic_singular_label() -> None:
    """D9: singular outro uses 'topic:' (not 'topics:')."""
    assert "  topic:           {topic}\n" in S.OUTRO_SINGULAR_TEMPLATE
    assert "topics:" not in S.OUTRO_SINGULAR_TEMPLATE


def test_outro_plural_uses_topics_plural_label_and_csv() -> None:
    """D9: plural outro uses 'topics:' with comma-separated names."""
    assert "  topics:          {topics_csv}\n" in S.OUTRO_PLURAL_TEMPLATE
    assert "  topic:           " not in S.OUTRO_PLURAL_TEMPLATE


def test_outro_singular_suggests_chat_and_sleep_with_topic_name() -> None:
    assert "Try `remory chat {topic}`" in S.OUTRO_SINGULAR_TEMPLATE
    assert "remory sleep {topic}" in S.OUTRO_SINGULAR_TEMPLATE


def test_outro_plural_suggests_first_topic_as_primary() -> None:
    """Plural outro names the first picked topic in the primary chat suggestion."""
    assert "Try `remory chat {first_topic}`" in S.OUTRO_PLURAL_TEMPLATE


def test_outro_both_end_with_doctor_safety_net() -> None:
    expected = "If something looks off, `remory doctor` will tell you.\n"
    assert S.OUTRO_SINGULAR_TEMPLATE.endswith(expected)
    assert S.OUTRO_PLURAL_TEMPLATE.endswith(expected)


# ---------------------------------------------------------------------------
# Interrupt + error messages
# ---------------------------------------------------------------------------


def test_pre_commit_interrupt_message_pins_no_files_written_phrasing() -> None:
    assert S.PRE_COMMIT_INTERRUPT_MESSAGE == (
        "Stopped. No files written. Run remory init when you're ready.\n"
    )


def test_during_commit_interrupt_message_pins_some_files_may_exist_phrasing() -> None:
    assert S.DURING_COMMIT_INTERRUPT_MESSAGE == (
        "Stopped mid-write. Some files may exist. Run remory doctor to inspect.\n"
    )


def test_three_strikes_message_pins_three_tries_phrasing() -> None:
    assert S.THREE_STRIKES_MESSAGE == (
        "Three tries — let's stop here. Run remory init again when you're ready.\n"
    )


# ---------------------------------------------------------------------------
# step_outro: pluralize wiring (D9) — one-topic vs multi-topic dispatch
# ---------------------------------------------------------------------------


def _quiet_console_buffer() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, color_system=None, no_color=True), buf


def test_step_outro_renders_singular_template_for_one_topic() -> None:
    console, buf = _quiet_console_buffer()
    step_outro(
        console=console,
        data_dir_str="/data",
        chosen_topics=["workout"],
        about_me_path_str="/data/about-me.md",
    )
    out = buf.getvalue()
    # Singular label + topic name interpolated.
    assert "topic:           workout" in out
    # Plural label must not appear.
    assert "topics:" not in out
    # Singular outro's chat suggestion uses the topic.
    assert "Try `remory chat workout`" in out


def test_step_outro_renders_plural_template_for_two_topics_with_first_as_primary() -> None:
    console, buf = _quiet_console_buffer()
    step_outro(
        console=console,
        data_dir_str="/data",
        chosen_topics=["job-profile", "workout"],
        about_me_path_str="/data/about-me.md",
    )
    out = buf.getvalue()
    # Plural label + CSV list (selection order).
    assert "topics:          job-profile, workout" in out
    assert "topic:           " not in out
    # First-picked topic is the primary chat suggestion.
    assert "Try `remory chat job-profile`" in out
