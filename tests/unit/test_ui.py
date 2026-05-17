"""Tests for :mod:`remory.ui` — TTY detection, doctor rendering, R4 sleep note."""

from __future__ import annotations

from pathlib import Path

import pytest

from remory.sleep.orchestrator import SleepResult, SleepStatus
from remory.ui import (
    CheckResult,
    CheckStatus,
    TopicsRow,
    is_narrow,
    is_tty,
    prompt_line,
    render_doctor_report,
    render_sleep_summary,
    render_topics_table,
    use_color,
)

# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def test_is_narrow_returns_true_for_columns_below_60() -> None:
    assert is_narrow(columns=40) is True


def test_is_narrow_returns_false_for_columns_at_or_above_60() -> None:
    assert is_narrow(columns=60) is False
    assert is_narrow(columns=120) is False


def test_is_tty_returns_false_for_string_io_stream() -> None:
    import io

    assert is_tty(io.StringIO()) is False


def test_use_color_returns_false_when_no_color_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    import io

    assert use_color(stream=io.StringIO()) is False


# ---------------------------------------------------------------------------
# Doctor report rendering
# ---------------------------------------------------------------------------


def test_render_doctor_report_clean_run_footer_says_youre_good() -> None:
    rows = [
        CheckResult(id="data_dir", status=CheckStatus.OK, label="data_dir", detail="/x"),
        CheckResult(id="config", status=CheckStatus.OK, label="config", detail="defaults"),
        CheckResult(
            id="claude_binary",
            status=CheckStatus.OK,
            label="claude binary",
            detail="/usr/bin/claude",
        ),
    ]
    out = render_doctor_report(results=rows, color=False)
    assert "3 checks, 0 warnings, 0 failures. You're good." in out


def test_render_doctor_report_includes_remediation_indented_with_arrow() -> None:
    rows = [
        CheckResult(
            id="claude_auth",
            status=CheckStatus.FAIL,
            label="claude auth",
            detail="not logged in",
            remediation=("run `claude` once interactively to log in.",),
        ),
    ]
    out = render_doctor_report(results=rows, color=False)
    assert "-> run `claude` once interactively to log in." in out


def test_render_doctor_report_footer_count_matches_rows_shown() -> None:
    """D9 reconciliation: footer says "5 checks" not "3" when 5 rows render."""
    rows = [
        CheckResult(
            id=f"row{i}",
            status=CheckStatus.OK,
            label=f"row{i}",
            detail=f"d{i}",
        )
        for i in range(5)
    ]
    out = render_doctor_report(results=rows, color=False)
    assert "5 checks" in out


def test_render_doctor_report_uses_lowercase_glyphs_when_color_false() -> None:
    rows = [
        CheckResult(id="a", status=CheckStatus.OK, label="a", detail="d"),
    ]
    out = render_doctor_report(results=rows, color=False)
    # ASCII fallback uses lowercase 'ok' / 'warn' / 'fail'.
    assert "ok" in out
    assert "OK ✓" not in out


def test_render_doctor_report_wraps_ok_in_ansi_green_when_color_true() -> None:
    rows = [
        CheckResult(id="a", status=CheckStatus.OK, label="a", detail="d"),
    ]
    out = render_doctor_report(results=rows, color=True)
    assert "\033[32mOK   \033[0m" in out


def test_render_doctor_report_wraps_fail_in_ansi_red_when_color_true() -> None:
    rows = [
        CheckResult(id="a", status=CheckStatus.FAIL, label="a", detail="d"),
    ]
    out = render_doctor_report(results=rows, color=True)
    assert "\033[31mFAIL \033[0m" in out


def test_render_doctor_report_wraps_warn_in_ansi_yellow_when_color_true() -> None:
    rows = [
        CheckResult(id="a", status=CheckStatus.WARN, label="a", detail="d"),
    ]
    out = render_doctor_report(results=rows, color=True)
    assert "\033[33mWARN \033[0m" in out


def test_render_doctor_report_leaves_skip_and_info_uncolored_when_color_true() -> None:
    rows = [
        CheckResult(id="a", status=CheckStatus.SKIP, label="a", detail="d"),
        CheckResult(id="b", status=CheckStatus.INFO, label="b", detail="d"),
    ]
    out = render_doctor_report(results=rows, color=True)
    assert "SKIP " in out
    assert "INFO " in out
    assert "\033[" not in out  # no ANSI escapes at all for these statuses


# ---------------------------------------------------------------------------
# R4 — locked sleep-output critique-skip note
# ---------------------------------------------------------------------------


def test_print_sleep_summary_success_with_warnings_critique_skip_renders_locked_note() -> None:
    """R4 (locked verbatim) — when SUCCESS_WITH_WARNINGS and critique
    failed (review_path is None), sleep output ends with the italic note.
    """
    result = SleepResult(
        status=SleepStatus.SUCCESS_WITH_WARNINGS,
        topic_name="workout",
        run_id="2026-05-09-093000",
        backup_path=None,
        review_path=None,
        consolidated_count=1,
        section_outcomes=(),
        notes=("critique failed: backend output empty",),
    )
    out = render_sleep_summary(result)
    # Verbatim per R4 — do not edit without updating consolidated plan §5.
    assert (
        "note: critique step couldn't run; state.md is up to date but _review.md\nwasn't refreshed."
    ) in out


def test_render_sleep_summary_success_renders_consolidated_count() -> None:
    result = SleepResult(
        status=SleepStatus.SUCCESS,
        topic_name="workout",
        run_id="run-1",
        backup_path=Path("/tmp/.bak"),
        review_path=Path("/tmp/_review.md"),
        consolidated_count=3,
        section_outcomes=(),
        notes=(),
    )
    out = render_sleep_summary(result)
    assert "Consolidated 3 pending entries" in out
    assert "/tmp/.bak" in out
    assert "/tmp/_review.md" in out


def test_render_sleep_summary_success_appends_closing_with_next_step_hints() -> None:
    """Sleep used to end abruptly at the Review path line. The closing
    block names the read commands and ends with a short warm sign-off
    so the run feels finished rather than cut off."""
    result = SleepResult(
        status=SleepStatus.SUCCESS,
        topic_name="job-profile",
        run_id="run-1",
        backup_path=Path("/tmp/.bak"),
        review_path=Path("/tmp/_review.md"),
        consolidated_count=1,
        section_outcomes=(),
        notes=(),
    )
    out = render_sleep_summary(result)
    assert "Read what's new: remory state job-profile" in out
    assert "Read the critic's notes: remory review job-profile" in out
    assert "See you soon." in out


def test_render_sleep_summary_success_no_review_omits_critic_hint_but_keeps_signoff() -> None:
    """When the schema's default_depth is single_pass (or critique
    failed), there's no _review.md to read — the closing should omit
    the critic-read line but still print the state-read line and the
    sign-off."""
    result = SleepResult(
        status=SleepStatus.SUCCESS,
        topic_name="workout",
        run_id="run-1",
        backup_path=Path("/tmp/.bak"),
        review_path=None,
        consolidated_count=1,
        section_outcomes=(),
        notes=(),
    )
    out = render_sleep_summary(result)
    assert "Read what's new: remory state workout" in out
    assert "Read the critic's notes" not in out
    assert "See you soon." in out


def test_render_sleep_summary_dry_run_skips_closing_signoff() -> None:
    """Dry-run didn't actually write anything; the warm "See you soon."
    closing would be misleading."""
    result = SleepResult(
        status=SleepStatus.SUCCESS,
        topic_name="workout",
        run_id="run-1",
        backup_path=None,
        review_path=None,
        consolidated_count=1,
        section_outcomes=(),
        notes=("DRY-RUN: no files written", "proposed_state_md:\n# foo\n"),
    )
    out = render_sleep_summary(result)
    assert "(dry run: no files written)" in out
    assert "See you soon." not in out
    assert "Read what's new" not in out


def test_render_sleep_summary_no_pending_emits_nothing_to_do_line() -> None:
    result = SleepResult(
        status=SleepStatus.NO_PENDING,
        topic_name="workout",
        run_id="run-1",
        backup_path=None,
        review_path=None,
        consolidated_count=0,
        section_outcomes=(),
        notes=(),
    )
    out = render_sleep_summary(result)
    assert "Nothing pending" in out


def test_render_sleep_summary_emits_drift_note_with_note_prefix() -> None:
    result = SleepResult(
        status=SleepStatus.SUCCESS_WITH_WARNINGS,
        topic_name="workout",
        run_id="run-1",
        backup_path=None,
        review_path=Path("/tmp/_review.md"),
        consolidated_count=1,
        section_outcomes=(),
        notes=("dropped drift section 'Notes' (not in schema; see logs)",),
    )
    out = render_sleep_summary(result)
    assert "note: dropped drift section 'Notes'" in out


# ---------------------------------------------------------------------------
# Topics table rendering
# ---------------------------------------------------------------------------


def test_render_topics_table_emits_no_topics_yet_when_rows_empty() -> None:
    out = render_topics_table([])
    assert "No topics yet" in out


def test_render_topics_table_aligns_columns_with_headers() -> None:
    rows = [
        TopicsRow(
            name="workout",
            schema_name="workout",
            pending=2,
            last_chat="2026-05-09T09:00:00+00:00",
            last_consolidated="—",
        ),
    ]
    out = render_topics_table(rows)
    assert "topic" in out and "schema" in out and "pending" in out
    assert "workout" in out


# ---------------------------------------------------------------------------
# prompt_line — raw read, no .strip()
# ---------------------------------------------------------------------------


def test_prompt_line_preserves_leading_and_trailing_whitespace() -> None:
    """Plan §7: prompt_line is the no-strip read so the wizard's validators
    can see embedded newlines / surrounding whitespace. Wrapping the test seam
    in a lambda lets us assert byte-equal that the input bytes pass through.
    """
    raw = "  Sam with surrounding spaces  "
    captured = prompt_line("name? ", input_fn=lambda: raw)
    assert captured == raw


def test_prompt_line_returns_empty_string_for_empty_input() -> None:
    """Empty input from input_fn returns empty string (validator decides
    what 'empty' means)."""
    captured = prompt_line("> ", input_fn=lambda: "")
    assert captured == ""
