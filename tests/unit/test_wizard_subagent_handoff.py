"""Run-directory handoff tests for the wizard subagent (Phase 6 §6.2).

Pins:
- ``parse_run_dir`` raises :class:`WizardAnswerParseError` with a
  ``kind`` discriminator (``"missing"`` / ``"invalid_json"`` /
  ``"validation"``).
- ``dump_recovery`` is robust to partial subagent output (writes a
  validation-error.txt even when answers.json is absent).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remory.wizard._subagent import (
    SubagentRunResult,
    WizardAnswerParseError,
    dump_recovery,
    parse_run_dir,
)


def _valid_answers_payload() -> dict[str, object]:
    return {
        "version": 1,
        "name": "Sam",
        "chosen_topics": ["workout"],
        "knobs_by_topic": {"workout": {"tone": "warm", "strictness": "balanced"}},
        "wish": "stop forgetting",
    }


def _write_valid_run_dir(run_dir: Path, *, letter: str = "Hi Sam.\n") -> None:
    (run_dir / "answers.json").write_text(json.dumps(_valid_answers_payload()), encoding="utf-8")
    (run_dir / "letter.md").write_text(letter, encoding="utf-8")


# ---------------------------------------------------------------------------
# parse_run_dir
# ---------------------------------------------------------------------------


def test_parse_run_dir_returns_answers_and_letter_when_both_files_valid(
    tmp_path: Path,
) -> None:
    _write_valid_run_dir(tmp_path, letter="Hi Sam. I'll keep what you bring.\n")
    result = parse_run_dir(tmp_path)
    assert isinstance(result, SubagentRunResult)
    assert result.answers.name == "Sam"
    assert result.letter == "Hi Sam. I'll keep what you bring.\n"


def test_parse_run_dir_raises_when_answers_json_missing(tmp_path: Path) -> None:
    (tmp_path / "letter.md").write_text("letter only\n", encoding="utf-8")
    with pytest.raises(WizardAnswerParseError) as ei:
        parse_run_dir(tmp_path)
    assert ei.value.kind == "missing"


def test_parse_run_dir_raises_when_letter_md_missing(tmp_path: Path) -> None:
    (tmp_path / "answers.json").write_text(json.dumps(_valid_answers_payload()), encoding="utf-8")
    with pytest.raises(WizardAnswerParseError) as ei:
        parse_run_dir(tmp_path)
    assert ei.value.kind == "missing"


def test_parse_run_dir_raises_when_answers_json_malformed_json(tmp_path: Path) -> None:
    (tmp_path / "answers.json").write_text("{not actually json", encoding="utf-8")
    (tmp_path / "letter.md").write_text("ok\n", encoding="utf-8")
    with pytest.raises(WizardAnswerParseError) as ei:
        parse_run_dir(tmp_path)
    assert ei.value.kind == "invalid_json"


def test_parse_run_dir_raises_when_answers_json_validation_fails(tmp_path: Path) -> None:
    bad = dict(_valid_answers_payload())
    bad["version"] = 99  # not Literal[1]
    (tmp_path / "answers.json").write_text(json.dumps(bad), encoding="utf-8")
    (tmp_path / "letter.md").write_text("ok\n", encoding="utf-8")
    with pytest.raises(WizardAnswerParseError) as ei:
        parse_run_dir(tmp_path)
    assert ei.value.kind == "validation"


# ---------------------------------------------------------------------------
# dump_recovery
# ---------------------------------------------------------------------------


def test_dump_recovery_writes_malformed_and_validation_error_when_both_present(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "answers.json").write_text("{bad json", encoding="utf-8")
    (run_dir / "letter.md").write_text("a letter\n", encoding="utf-8")
    exc = WizardAnswerParseError("answers.json is not valid JSON: ...", kind="invalid_json")

    recovery_dir = dump_recovery(data_dir, run_dir, exc)
    assert recovery_dir.is_dir()
    assert (recovery_dir / "answers.json.malformed").read_text(encoding="utf-8") == "{bad json"
    assert (recovery_dir / "letter.md").read_text(encoding="utf-8") == "a letter\n"
    err_text = (recovery_dir / "validation-error.txt").read_text(encoding="utf-8")
    assert "answers.json is not valid JSON" in err_text
    # Recovery dir is under <data_dir>/.remory/wizard-recovery/<ts>/.
    assert recovery_dir.parent.name == "wizard-recovery"
    assert recovery_dir.parent.parent.name == ".remory"


def test_dump_recovery_omits_letter_when_absent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # Only answers.json exists.
    (run_dir / "answers.json").write_text("{still bad", encoding="utf-8")
    exc = WizardAnswerParseError("answers.json is not valid JSON", kind="invalid_json")

    recovery_dir = dump_recovery(data_dir, run_dir, exc)
    assert (recovery_dir / "answers.json.malformed").exists()
    assert not (recovery_dir / "letter.md").exists()
    assert (recovery_dir / "validation-error.txt").exists()
