"""Refresh-pass behaviour tests.

These exercise the Python functions :func:`remory.claude_assets.refresh`
(combined ``.claude/`` + per-topic) and
:func:`remory.claude_assets.install_data_dir_templates` (just the
``.claude/`` side) directly. The CLI-flag-driven counterparts
(``remory init --refresh``) land in Batch 3; those test names are
preserved here as ``pytest.skip`` markers so the TODO is visible.

See plan §11.1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from remory.claude_assets import install_data_dir_templates, refresh
from remory.data_templates import iter_template_relpaths, read_template_bytes
from remory.locking import topic_lock
from remory.schema import load_builtin
from remory.topic import Knobs, TopicMeta, write_meta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_topic(
    data_dir: Path,
    schema_name: str = "workout",
    *,
    tone: Literal["warm", "balanced", "direct"] = "warm",
    strictness: Literal["gentle", "balanced", "rigorous"] = "balanced",
) -> Path:
    """Build a real topic directory under <data_dir>/topics/<schema_name>."""
    schema = load_builtin(schema_name)
    topic_dir = data_dir / "topics" / schema_name
    topic_dir.mkdir(parents=True, exist_ok=True)
    knobs = Knobs(tone=tone, strictness=strictness)
    meta = TopicMeta(
        schema=schema_name,
        schema_version=schema.version,
        created=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        pending_count=0,
        total_entries=0,
        knobs=knobs,
    )
    with topic_lock(topic_dir):
        write_meta(topic_dir, meta)
    return topic_dir


# ---------------------------------------------------------------------------
# Python-level refresh() / install_data_dir_templates() tests
# ---------------------------------------------------------------------------


def test_init_refresh_writes_all_templates_when_data_dir_clean(tmp_path: Path) -> None:
    result = install_data_dir_templates(tmp_path)
    expected_paths = {tmp_path / rel for rel in iter_template_relpaths()}
    assert set(result.written) == expected_paths
    assert result.overwritten == ()


def test_init_refresh_skips_stamped_but_edited_file_and_returns_conflict(
    tmp_path: Path,
) -> None:
    # First-time install.
    install_data_dir_templates(tmp_path)
    # Edit one stamped file without bumping its stamp.
    target = tmp_path / ".claude" / "agents" / "extractor.md"
    original = target.read_bytes()
    # Append a user paragraph; the stamp comment stays put.
    target.write_bytes(original + b"\nuser-added paragraph\n")
    # Default refresh — no force.
    result = install_data_dir_templates(tmp_path, force=False)
    # The edited file is NOT overwritten; it lands in skipped with the
    # conflict reason.
    conflicts = [s for s in result.skipped if s.reason == "stamped-but-edited"]
    assert len(conflicts) == 1
    assert conflicts[0].path == target
    # Bytes on disk untouched.
    assert target.read_bytes() == original + b"\nuser-added paragraph\n"


def test_init_refresh_force_overwrites_stamped_but_edited_and_writes_bak(
    tmp_path: Path,
) -> None:
    install_data_dir_templates(tmp_path)
    target = tmp_path / ".claude" / "agents" / "extractor.md"
    original = target.read_bytes()
    target.write_bytes(original + b"\nuser-added paragraph\n")

    result = install_data_dir_templates(tmp_path, force=True)
    assert target in result.overwritten
    # Bytes are back to the bundled form.
    assert target.read_bytes() == read_template_bytes(".claude/agents/extractor.md")
    # A .bak was saved. The flattened name includes the .claude__
    # prefix because the relpath flattener starts from data_dir.
    backups_dir = tmp_path / ".claude" / ".backups"
    baks = list(backups_dir.glob(".claude__agents__extractor.md.*.bak"))
    assert len(baks) == 1
    # The .bak carries the user-edited bytes, not the bundled ones.
    assert baks[0].read_bytes() == original + b"\nuser-added paragraph\n"


def test_init_refresh_writes_bak_for_stamped_older_overwrite_without_force(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".claude" / "agents" / "merger.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"<!-- remory: template_version=0 -->\nold merger\n")

    result = install_data_dir_templates(tmp_path, force=False)
    assert target in result.overwritten
    backups_dir = tmp_path / ".claude" / ".backups"
    baks = list(backups_dir.glob(".claude__agents__merger.md.*.bak"))
    assert len(baks) == 1


def test_init_refresh_preserves_unstamped_file_even_with_force(
    tmp_path: Path,
) -> None:
    """Pin D5: --force does NOT overwrite unstamped files."""
    target = tmp_path / ".claude" / "agents" / "wizard.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"unstamped hand-edited prompt body\n")

    result = install_data_dir_templates(tmp_path, force=True)
    # Not in written or overwritten.
    assert target not in result.written
    assert target not in result.overwritten
    # In skipped with the preserved reason.
    preserved = [s for s in result.skipped if s.path == target]
    assert len(preserved) == 1
    assert preserved[0].reason == "unstamped-preserved"
    # And the bytes are untouched.
    assert target.read_bytes() == b"unstamped hand-edited prompt body\n"


def test_init_refresh_treats_on_disk_version_greater_than_bundle_as_warn_not_overwrite(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / ".claude" / "agents" / "critic.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    # A newer-stamped file on disk — current PRODUCTION_TEMPLATE_VERSION
    # is 1, so a 99 stamp is "newer".
    newer_payload = b"<!-- remory: template_version=99 -->\nfuture critic\n"
    target.write_bytes(newer_payload)

    with caplog.at_level("WARNING", logger="remory.claude_assets"):
        result = install_data_dir_templates(tmp_path, force=False)
    # Not overwritten; appears in skipped with newer-on-disk reason.
    assert target not in result.overwritten
    newer = [s for s in result.skipped if s.path == target]
    assert len(newer) == 1
    assert newer[0].reason == "newer-on-disk"
    assert newer[0].on_disk_version == 99
    # Bytes untouched.
    assert target.read_bytes() == newer_payload
    # Even with --force, the newer-on-disk policy still refuses.
    result2 = install_data_dir_templates(tmp_path, force=True)
    assert target not in result2.overwritten


def test_init_refresh_continues_when_one_topic_has_malformed_meta_yaml_and_reports_skip(
    tmp_path: Path,
) -> None:
    # Two topics: one valid, one with broken meta.yaml. refresh() must
    # continue past the malformed topic.
    good_dir = _make_topic(tmp_path, "workout")
    bad_dir = tmp_path / "topics" / "broken"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "meta.yaml").write_bytes(b"not: [valid yaml: at all\n")

    result = refresh(tmp_path, force=False, dry_run=False)
    # The good topic's CLAUDE.md was written.
    good_md = good_dir / "CLAUDE.md"
    assert good_md in result.written
    assert good_md.is_file()
    # The bad topic surfaces as a skip with meta-malformed reason.
    bad_md = bad_dir / "CLAUDE.md"
    bad_skips = [s for s in result.skipped if s.path == bad_md]
    assert len(bad_skips) == 1
    assert bad_skips[0].reason == "meta-malformed"
    # The bad topic gets NO CLAUDE.md written.
    assert not bad_md.exists()


def test_init_refresh_regenerates_per_topic_claude_md_when_knobs_changed_in_meta(
    tmp_path: Path,
) -> None:
    """First refresh writes CLAUDE.md; then we change knobs in meta and
    --force a re-render."""
    topic_dir = _make_topic(tmp_path, "workout", tone="warm", strictness="balanced")
    first = refresh(tmp_path, force=False, dry_run=False)
    target = topic_dir / "CLAUDE.md"
    assert target in first.written
    original = target.read_bytes()

    # Update meta with new knobs.
    schema = load_builtin("workout")
    new_meta = TopicMeta(
        schema="workout",
        schema_version=schema.version,
        created=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        pending_count=0,
        total_entries=0,
        knobs=Knobs(tone="direct", strictness="rigorous"),
    )
    with topic_lock(topic_dir):
        write_meta(topic_dir, new_meta)

    # Without --force, the byte-drift on a current-stamp file is a
    # conflict (skip). With --force, it's overwrite.
    skipped_result = refresh(tmp_path, force=False, dry_run=False)
    assert target not in skipped_result.overwritten
    skip_entries = [s for s in skipped_result.skipped if s.path == target]
    assert len(skip_entries) == 1
    assert skip_entries[0].reason == "stamped-but-edited"

    forced = refresh(tmp_path, force=True, dry_run=False)
    assert target in forced.overwritten
    # Bytes differ from the original.
    assert target.read_bytes() != original


def test_init_refresh_regenerates_per_topic_claude_md_when_template_version_older(
    tmp_path: Path,
) -> None:
    topic_dir = _make_topic(tmp_path, "workout")
    target = topic_dir / "CLAUDE.md"
    # Seed an older-stamped CLAUDE.md.
    target.write_bytes(b"<!-- remory: template_version=0 -->\nstale body\n")
    result = refresh(tmp_path, force=False, dry_run=False)
    assert target in result.overwritten


def test_init_refresh_skips_user_edited_per_topic_claude_md_without_force(
    tmp_path: Path,
) -> None:
    topic_dir = _make_topic(tmp_path, "workout")
    target = topic_dir / "CLAUDE.md"
    # First-time write.
    refresh(tmp_path, force=False, dry_run=False)
    # User edits the file but leaves the stamp.
    body = target.read_bytes() + b"\nuser appendix\n"
    target.write_bytes(body)
    # Without --force, refresh skips with stamped-but-edited.
    result = refresh(tmp_path, force=False, dry_run=False)
    edits = [s for s in result.skipped if s.path == target]
    assert len(edits) == 1
    assert edits[0].reason == "stamped-but-edited"
    assert target.read_bytes() == body  # untouched


def test_init_refresh_does_not_touch_state_md_or_meta_yaml_or_raw_dir(
    tmp_path: Path,
) -> None:
    topic_dir = _make_topic(tmp_path, "workout")
    # Seed state.md, raw/, and edit meta.yaml's mtime.
    state_md = topic_dir / "state.md"
    state_md.write_bytes(b"my state\n")
    raw_dir = topic_dir / "raw" / "2026"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_entry = raw_dir / "2026-05-09-1000.md"
    raw_entry.write_bytes(b"raw\n")
    meta_path = topic_dir / "meta.yaml"
    pre_meta_bytes = meta_path.read_bytes()

    refresh(tmp_path, force=True, dry_run=False)
    # Files untouched.
    assert state_md.read_bytes() == b"my state\n"
    assert raw_entry.read_bytes() == b"raw\n"
    assert meta_path.read_bytes() == pre_meta_bytes


def test_init_refresh_dry_run_writes_nothing_when_changes_pending(
    tmp_path: Path,
) -> None:
    # Clean data dir — refresh would write everything.
    result = refresh(tmp_path, force=False, dry_run=True)
    # The result *describes* pending writes,
    assert len(result.written) > 0
    # but nothing is actually on disk.
    for relpath in iter_template_relpaths():
        assert not (tmp_path / relpath).exists()
    # And no .backups dir was created.
    assert not (tmp_path / ".claude" / ".backups").exists()


def test_init_refresh_dry_run_writes_nothing_when_nothing_to_change(
    tmp_path: Path,
) -> None:
    # Install once.
    refresh(tmp_path, force=False, dry_run=False)
    # Now dry-run a second time — should report only unchanged.
    result = refresh(tmp_path, force=False, dry_run=True)
    assert result.written == ()
    assert result.overwritten == ()
    # Every skipped is reason="unchanged".
    assert {s.reason for s in result.skipped} == {"unchanged"} or result.skipped == ()


def test_init_refresh_dry_run_lists_each_category_correctly(tmp_path: Path) -> None:
    # Set up a mixed state: one missing (delete one), one stamped-older,
    # one unstamped (preserve), one untouched.
    refresh(tmp_path, force=False, dry_run=False)
    # Delete one — refresh should report it as "write" (missing).
    (tmp_path / ".claude" / "commands" / "sleep.md").unlink()
    # Make one stamped-older.
    older = tmp_path / ".claude" / "commands" / "state.md"
    older.write_bytes(b"<!-- remory: template_version=0 -->\nold state cmd\n")
    # Make one unstamped (overwriting the canonical file).
    unstamped = tmp_path / ".claude" / "commands" / "recent.md"
    unstamped.write_bytes(b"user-modified recent\n")

    result = refresh(tmp_path, force=False, dry_run=True)
    # The missing file shows in written (would-be-written).
    paths_written = {p for p in result.written}
    assert (tmp_path / ".claude" / "commands" / "sleep.md") in paths_written
    # The stamped-older shows in overwritten (would-be-overwritten).
    assert older in set(result.overwritten)
    # The unstamped is in skipped with preserved reason.
    preserved = [s for s in result.skipped if s.path == unstamped]
    assert len(preserved) == 1
    assert preserved[0].reason == "unstamped-preserved"


# ---------------------------------------------------------------------------
# CLI-flag-dependent tests (Phase 6 Batch 3)
# ---------------------------------------------------------------------------


def test_init_refresh_dry_run_exits_zero_in_all_states(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``remory init --refresh --dry-run`` always exits 0.

    Covered by both: (a) clean data dir, (b) already-installed data dir.
    """
    from typer.testing import CliRunner

    from remory.cli import app

    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)

    runner = CliRunner()
    # (a) clean.
    result_a = runner.invoke(app, ["init", "--refresh", "--dry-run"])
    assert result_a.exit_code == 0, result_a.output
    # (b) already installed.
    install_data_dir_templates(tmp_path / "data")
    result_b = runner.invoke(app, ["init", "--refresh", "--dry-run"])
    assert result_b.exit_code == 0, result_b.output


def test_init_dry_run_without_refresh_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``remory init --dry-run`` (no --refresh) → exit 2."""
    from typer.testing import CliRunner

    from remory.cli import app

    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)

    runner = CliRunner()
    result = runner.invoke(app, ["init", "--dry-run"])
    assert result.exit_code == 2
    # Verbatim per plan §5.10.
    assert "--dry-run requires --refresh" in (result.output + (result.stderr or ""))


def test_init_refresh_cli_writes_templates_on_clean_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`remory init --refresh` actually installs the templates."""
    from typer.testing import CliRunner

    from remory.cli import app

    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)

    runner = CliRunner()
    result = runner.invoke(app, ["init", "--refresh"])
    assert result.exit_code == 0, result.output
    # The bundled tree is on disk.
    for rel in iter_template_relpaths():
        assert (tmp_path / "data" / rel).exists(), rel
    # The header points at the resolved .claude/ path.
    claude_root = tmp_path / "data" / ".claude"
    assert f"Refreshed .claude/ templates at {claude_root}" in result.output


def test_init_refresh_cli_dry_run_writes_nothing_to_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`remory init --refresh --dry-run` describes actions but writes nothing."""
    from typer.testing import CliRunner

    from remory.cli import app

    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)

    runner = CliRunner()
    result = runner.invoke(app, ["init", "--refresh", "--dry-run"])
    assert result.exit_code == 0, result.output
    # "Would update" framing per §5.10.
    assert "Would update .claude/ templates" in result.output
    assert "Run without --dry-run to apply" in result.output
    # No files actually written.
    for rel in iter_template_relpaths():
        assert not (tmp_path / "data" / rel).exists(), rel


def test_init_refresh_cli_reports_up_to_date_when_nothing_to_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After install, a second --refresh prints the "up to date" copy."""
    from typer.testing import CliRunner

    from remory.cli import app

    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)

    install_data_dir_templates(tmp_path / "data")
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--refresh"])
    assert result.exit_code == 0, result.output
    # Verbatim §5.10: ".claude/ at <data_dir>/.claude/ is up to date."
    assert "is up to date." in result.output
    # And the per-topic CLAUDE.md line is also present.
    assert "Per-topic CLAUDE.md is up to date for all" in result.output
