"""Doctor checks for bundled-template drift and per-topic CLAUDE.md drift.

Plan §11.1 — six tests. Verifies the §5.11 messaging surface end-to-end
on the two new check functions; the §5.11 strings are verbatim.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from remory.claude_assets import install_data_dir_templates
from remory.commands.doctor_cmd import (
    _check_claude_templates,
    _check_per_topic_claude_md,
)
from remory.commands.init_cmd import run_init
from remory.ui import CheckStatus


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def test_doctor_reports_ok_when_every_bundled_template_byte_matches_disk(
    isolated_xdg: Path,
) -> None:
    data_dir = isolated_xdg / "data"
    install_data_dir_templates(data_dir)
    result = _check_claude_templates(data_dir)
    assert result.status is CheckStatus.OK
    # Plan §5.11: "current (12 file(s) match bundle)".
    assert "current" in result.detail
    assert "match bundle" in result.detail


def test_doctor_warns_when_stamped_template_edited_on_disk_and_leads_with_force(
    isolated_xdg: Path,
) -> None:
    data_dir = isolated_xdg / "data"
    install_data_dir_templates(data_dir)
    edited = data_dir / ".claude" / "agents" / "extractor.md"
    original = edited.read_bytes()
    edited.write_bytes(original + b"\nuser appendix paragraph\n")
    result = _check_claude_templates(data_dir)
    assert result.status is CheckStatus.WARN
    # The detail mentions the offending file and "edited after stamping".
    assert "edited after stamping" in result.detail
    assert "agents/extractor.md" in result.detail
    # Remediation leads with the primary action (--refresh --force) and
    # mentions --dry-run as the optional preview, not the other way
    # around. A user reading the hint top-down should see the fix first.
    rem_text = "\n".join(result.remediation)
    assert "--refresh --force" in rem_text
    assert "--dry-run" in rem_text
    assert rem_text.index("--force") < rem_text.index("--dry-run"), (
        "primary action (--force) must come before the optional preview (--dry-run)"
    )


def test_doctor_warns_when_one_topic_claude_md_stale_and_names_count_not_all_topics(
    isolated_xdg: Path,
) -> None:
    data_dir = isolated_xdg / "data"
    install_data_dir_templates(data_dir)
    run_init(topic_name="workout", schema_name="workout")
    run_init(topic_name="coaching", schema_name="coaching")
    # Corrupt one topic's CLAUDE.md (still stamped, but bytes differ).
    target = data_dir / "topics" / "workout" / "CLAUDE.md"
    body = target.read_text(encoding="utf-8")
    target.write_text(body + "\nuser appendix\n", encoding="utf-8")

    result = _check_per_topic_claude_md(data_dir)
    assert result.status is CheckStatus.WARN
    # Names the stale topic, not all topics.
    assert "1 of 2 topic(s) stale" in result.detail
    assert "workout" in result.detail
    assert "coaching" not in result.detail


def test_doctor_fails_when_settings_json_missing(
    isolated_xdg: Path,
) -> None:
    data_dir = isolated_xdg / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    result = _check_claude_templates(data_dir)
    assert result.status is CheckStatus.FAIL
    assert ".claude/settings.json missing" in result.detail
    # Per §5.11: "run `remory init` to recreate".
    assert any("remory init" in r for r in result.remediation)


def test_doctor_fails_when_settings_json_malformed_and_remediation_mentions_force(
    isolated_xdg: Path,
) -> None:
    data_dir = isolated_xdg / "data"
    install_data_dir_templates(data_dir)
    # Corrupt settings.json with non-JSON bytes.
    settings = data_dir / ".claude" / "settings.json"
    settings.write_text("not json at all { ::", encoding="utf-8")
    result = _check_claude_templates(data_dir)
    assert result.status is CheckStatus.FAIL
    # Per §5.11: "malformed: <one-line error>".
    assert "malformed" in result.detail
    rem_text = "\n".join(result.remediation)
    assert "remory init --refresh --force" in rem_text
    # ".bak saved" appears in the remediation per §5.11.
    assert ".bak" in rem_text


def test_doctor_summary_line_for_topics_is_single_line_regardless_of_topic_count(
    isolated_xdg: Path,
) -> None:
    """The per-topic CLAUDE.md check is a single summary row, not one per topic."""
    data_dir = isolated_xdg / "data"
    install_data_dir_templates(data_dir)
    run_init(topic_name="workout", schema_name="workout")
    run_init(topic_name="coaching", schema_name="coaching")
    run_init(topic_name="job-profile", schema_name="job-profile")
    result = _check_per_topic_claude_md(data_dir)
    # One row, no newlines in the detail body — the renderer is the
    # only place line breaks appear.
    assert "\n" not in result.detail
    assert result.status is CheckStatus.OK
    assert "current for all 3 topic(s)" in result.detail
