"""Row-by-row tests for the §6 error mapping table.

These pin the binding decision rows: D6 (topic-state preconditions),
D7 (existing-topic refusal wording), and R3 (CritiqueError contract
reminder).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remory.backends.base import (
    BackendInvocationError,
    BackendNotFoundError,
    BackendOutputError,
    BackendTimeoutError,
)
from remory.cli.errors import (
    TopicExistsError,
    TopicIncompleteError,
    TopicMissingError,
    format_error,
)
from remory.config import ConfigError
from remory.locking import LockBusyError
from remory.raw import RawWriteError
from remory.schema import SchemaError
from remory.sleep.critique import CritiqueError
from remory.sleep.extract import ExtractError
from remory.sleep.merge import MergeError
from remory.sleep.orchestrator import SleepError
from remory.state import StateParseError
from remory.topic import TopicMetaError
from remory.wizard import WizardNotBuiltError


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


# ---------------------------------------------------------------------------
# D6 + D7 + R2/R3
# ---------------------------------------------------------------------------


def test_format_error_topic_missing_returns_init_hint_with_existing_list_and_exit_2(
    data_dir: Path,
) -> None:
    exc = TopicMissingError("nope", existing_topics=("coaching", "workout"))
    msg, code = format_error(exc, data_dir=data_dir)
    assert code == 2
    assert "doesn't exist yet" in msg
    assert "Run remory init nope" in msg
    assert "coaching, workout" in msg


def test_format_error_topic_missing_with_zero_topics_uses_no_topics_phrasing(
    data_dir: Path,
) -> None:
    exc = TopicMissingError("nope", existing_topics=())
    msg, code = format_error(exc, data_dir=data_dir)
    assert code == 2
    assert "Run remory init to set one up." in msg


def test_format_error_topic_incomplete_points_at_doctor_not_init_to_avoid_overwrite(
    data_dir: Path,
) -> None:
    exc = TopicIncompleteError("workout", "state.md missing")
    msg, code = format_error(exc, data_dir=data_dir)
    assert code == 2
    assert "incomplete" in msg
    assert "remory doctor" in msg
    assert "init could overwrite partial files" in msg


def test_format_error_topic_exists_returns_pinned_three_line_d7_wording_and_exit_1(
    tmp_path: Path,
) -> None:
    topic_dir = tmp_path / "topics" / "workout"
    exc = TopicExistsError("workout", topic_dir)
    msg, code = format_error(exc, data_dir=tmp_path)
    assert code == 1
    # D7 verbatim: 3 lines containing the path and the kebab-cased commands.
    assert "Topic 'workout' already exists at" in msg
    assert f"rm -rf {topic_dir}" in msg
    assert "remory init\nworkout` again" in msg
    assert "remory init <other>" in msg


def test_format_error_wizard_not_built_returns_r2_wording_and_exit_2(
    data_dir: Path,
) -> None:
    exc = WizardNotBuiltError(
        "The interactive wizard isn't built yet. For now, pass --schema to pick a\n"
        "built-in: --schema job-profile, --schema workout, or --schema coaching."
    )
    msg, code = format_error(exc, data_dir=data_dir)
    assert code == 2
    assert "interactive wizard isn't built yet" in msg
    assert "--schema job-profile" in msg


def test_format_error_critique_error_is_contract_reminder_returns_zero_exit_code(
    data_dir: Path,
) -> None:
    """R3: CritiqueError should never reach the CLI; if it does, treat as no-op."""
    exc = CritiqueError("oops")
    msg, code = format_error(exc, data_dir=data_dir)
    assert code == 0
    assert msg == ""


# ---------------------------------------------------------------------------
# Backend rows
# ---------------------------------------------------------------------------


def test_format_error_backend_not_found_returns_install_hint_and_exit_3(
    data_dir: Path,
) -> None:
    msg, code = format_error(BackendNotFoundError("..."), data_dir=data_dir)
    assert code == 3
    assert "claude isn't on your PATH" in msg


def test_format_error_backend_timeout_returns_retry_hint_and_exit_5(
    data_dir: Path,
) -> None:
    msg, code = format_error(BackendTimeoutError("after 30s"), data_dir=data_dir)
    assert code == 5
    assert "didn't respond" in msg


def test_format_error_backend_invocation_error_truncates_stderr_to_six_lines(
    data_dir: Path,
) -> None:
    tail = "\n".join(f"line {i}" for i in range(20))
    exc = BackendInvocationError("nope", exit_code=2, stderr_tail=tail)
    msg, code = format_error(exc, data_dir=data_dir)
    assert code == 5
    # We trim to last 6 lines.
    body_lines = [ln for ln in msg.splitlines() if ln.startswith("line ")]
    assert len(body_lines) == 6
    assert body_lines[-1] == "line 19"


def test_format_error_backend_output_error_returns_rare_phrasing_and_exit_5(
    data_dir: Path,
) -> None:
    msg, code = format_error(BackendOutputError("garbled"), data_dir=data_dir)
    assert code == 5
    assert "Rare" in msg


def test_format_error_lock_busy_returns_progress_msg_and_exit_6(data_dir: Path) -> None:
    msg, code = format_error(LockBusyError("topic foo is locked"), data_dir=data_dir)
    assert code == 6
    assert "Another remory operation" in msg


# ---------------------------------------------------------------------------
# Sleep + data parse rows
# ---------------------------------------------------------------------------


def test_format_error_sleep_error_extract_stage_returns_topic_specific_retry_hint(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "topics" / "workout" / "state.md"
    state_path.parent.mkdir(parents=True)
    exc = SleepError(
        "extract failed",
        backup_path=None,
        state_path=state_path,
        stage="extract",
        cause=None,
    )
    msg, code = format_error(exc, data_dir=tmp_path)
    assert code == 7
    assert "Sleep couldn't read what was new in 'workout'" in msg
    assert "remory sleep workout" in msg


def test_format_error_sleep_error_merge_stage_includes_backup_path_when_present(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "topics" / "workout" / "state.md"
    state_path.parent.mkdir(parents=True)
    backup = tmp_path / "topics" / "workout" / ".backups" / "state.md.2026-05-09.bak"
    exc = SleepError(
        "merge failed",
        backup_path=backup,
        state_path=state_path,
        stage="merge",
        cause=None,
    )
    msg, code = format_error(exc, data_dir=tmp_path)
    assert code == 7
    assert "data is safe" in msg
    assert str(backup) in msg


def test_format_error_extract_error_returns_two_strikes_phrasing_and_exit_7(
    data_dir: Path,
) -> None:
    msg, code = format_error(ExtractError("twice"), data_dir=data_dir)
    assert code == 7
    assert "wasn't valid extraction output, twice" in msg


def test_format_error_merge_error_returns_bug_phrasing_and_exit_7(data_dir: Path) -> None:
    msg, code = format_error(MergeError("internal"), data_dir=data_dir)
    assert code == 7
    assert "This is a bug" in msg


def test_format_error_topic_meta_error_returns_doctor_hint_and_exit_8(
    tmp_path: Path,
) -> None:
    meta_path = tmp_path / "topics" / "workout" / "meta.yaml"
    meta_path.parent.mkdir(parents=True)
    msg, code = format_error(TopicMetaError(meta_path, "schema_version invalid"), data_dir=tmp_path)
    assert code == 8
    assert "meta.yaml for 'workout'" in msg


def test_format_error_state_parse_error_returns_backups_pointer_and_exit_8(
    data_dir: Path,
) -> None:
    msg, code = format_error(StateParseError("bad fence"), data_dir=data_dir)
    assert code == 8
    assert ".backups" in msg


def test_format_error_schema_error_strips_in_source_suffix(data_dir: Path) -> None:
    body = (
        "Unknown schema 'jobprofile'.\n\n"
        "Did you mean: job-profile?\n\n"
        "Available built-in schemas: coaching, job-profile, workout."
    )
    msg, code = format_error(SchemaError("jobprofile", body), data_dir=data_dir)
    assert code == 2
    # The "(in jobprofile)" suffix appended by SchemaError must be stripped.
    assert "(in jobprofile)" not in msg
    assert "Did you mean: job-profile?" in msg


def test_format_error_raw_write_error_returns_disk_full_hint_and_exit_1(
    data_dir: Path,
) -> None:
    msg, code = format_error(RawWriteError("disk full"), data_dir=data_dir)
    assert code == 1
    assert "permissions issue" in msg


def test_format_error_config_error_includes_path_and_exit_9(
    tmp_path: Path,
) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[ui]\nemoji = 'not a bool'\n", encoding="utf-8")
    from remory.config import load_config

    try:
        load_config(cfg_path)
    except ConfigError as exc:
        msg, code = format_error(exc, data_dir=tmp_path)
        assert code == 9
        assert "config.toml has a problem" in msg
        assert str(cfg_path) in msg
        return
    pytest.fail("expected ConfigError")


def test_format_error_keyboard_interrupt_returns_empty_message_and_exit_130(
    data_dir: Path,
) -> None:
    msg, code = format_error(KeyboardInterrupt(), data_dir=data_dir)
    assert msg == ""
    assert code == 130


def test_format_error_unknown_exception_returns_unexpected_phrasing_and_exit_99(
    data_dir: Path,
) -> None:
    class Weird(Exception):
        pass

    msg, code = format_error(Weird("???"), data_dir=data_dir)
    assert code == 99
    assert "Something unexpected went wrong" in msg
