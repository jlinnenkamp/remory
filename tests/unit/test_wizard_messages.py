"""Locked-string assertions for the wizard's user-facing text.

After Phase 6 the wizard's voice belongs to the ``wizard.md`` subagent;
the only Python-owned strings are the SIGINT pre/during-commit
interrupts, the partial-failure templates, the about-me failure
message, the outro templates (singular/plural), and the two new Phase 6
strings (preflight doctor pointer + recovery dir template).

This file pins each kept-byte string. Phase 5's prompts/preambles are
gone; the corresponding tests were deleted with the strings.
"""

from __future__ import annotations

from remory.wizard import _strings as S

# ---------------------------------------------------------------------------
# Outro (pluralize-aware)
# ---------------------------------------------------------------------------


def test_outro_singular_uses_topic_singular_label() -> None:
    """Singular outro uses 'topic:' (not 'topics:')."""
    assert "  topic:           {topic}\n" in S.OUTRO_SINGULAR_TEMPLATE
    assert "topics:" not in S.OUTRO_SINGULAR_TEMPLATE


def test_outro_plural_uses_topics_plural_label_and_csv() -> None:
    """Plural outro uses 'topics:' with comma-separated names."""
    assert "  topics:          {topics_csv}\n" in S.OUTRO_PLURAL_TEMPLATE
    assert "  topic:           " not in S.OUTRO_PLURAL_TEMPLATE


def test_outro_singular_suggests_chat_and_sleep_with_topic_name() -> None:
    assert "Try `remory chat {topic}`" in S.OUTRO_SINGULAR_TEMPLATE
    assert "remory sleep {topic}" in S.OUTRO_SINGULAR_TEMPLATE


def test_outro_plural_suggests_first_topic_as_primary() -> None:
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


def test_partial_failure_with_prior_template_carries_failed_and_prior_placeholders() -> None:
    assert "{failed}" in S.PARTIAL_FAILURE_WITH_PRIOR_TEMPLATE
    assert "{prior}" in S.PARTIAL_FAILURE_WITH_PRIOR_TEMPLATE
    assert "Stopped mid-write at topic '{failed}'" in S.PARTIAL_FAILURE_WITH_PRIOR_TEMPLATE


def test_partial_failure_no_prior_template_carries_only_failed_placeholder() -> None:
    assert "{failed}" in S.PARTIAL_FAILURE_NO_PRIOR_TEMPLATE
    assert "{prior}" not in S.PARTIAL_FAILURE_NO_PRIOR_TEMPLATE


def test_about_me_failure_message_pins_phrasing() -> None:
    assert S.ABOUT_ME_FAILURE_MESSAGE == (
        "All topics created, but about-me.md couldn't be written. Run remory doctor.\n"
    )


# ---------------------------------------------------------------------------
# Phase 6 — new strings
# ---------------------------------------------------------------------------


def test_precondition_needs_doctor_message_pins_three_line_doctor_pointer() -> None:
    assert S.PRECONDITION_NEEDS_DOCTOR_MESSAGE == (
        "Remory needs the claude CLI to be installed and logged in before the wizard can run.\n"
        "Run: remory doctor\n"
        "Then re-run: remory init\n"
    )


def test_recovery_message_template_pins_recovery_dir_placeholder_and_phrasing() -> None:
    assert "{recovery_dir}" in S.RECOVERY_MESSAGE_TEMPLATE
    assert S.RECOVERY_MESSAGE_TEMPLATE == (
        "The wizard couldn't produce valid answers (tried twice).\n"
        "What you said is saved at:\n"
        "  {recovery_dir}\n"
        "No topic files were written. You can re-run `remory init` to try again.\n"
    )
