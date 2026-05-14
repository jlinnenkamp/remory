"""Tests for :func:`remory.claude_assets.install_data_dir_templates`.

Covers the policy table from plan §9 at the per-file level
(``refresh()`` combinatorics live in ``test_init_refresh.py``).

See plan §11.1.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remory.claude_assets import (
    PRODUCTION_TEMPLATE_VERSION,
    TEMPLATE_VERSION_KEY,
    install_data_dir_templates,
)
from remory.data_templates import iter_template_relpaths, read_template_bytes


def _all_bundled_relpaths() -> list[str]:
    return list(iter_template_relpaths())


def test_install_data_dir_templates_first_time_writes_all_and_stamps(
    tmp_path: Path,
) -> None:
    result = install_data_dir_templates(tmp_path)
    # Every bundled file should be reported as written.
    expected = {tmp_path / rel for rel in _all_bundled_relpaths()}
    assert set(result.written) == expected
    assert result.overwritten == ()
    # Each .md file on disk carries the stamp; settings.json carries the key.
    for rel in _all_bundled_relpaths():
        on_disk = (tmp_path / rel).read_bytes()
        if rel.endswith(".json"):
            payload = json.loads(on_disk.decode("utf-8"))
            assert isinstance(payload, dict)
            assert payload[TEMPLATE_VERSION_KEY] == PRODUCTION_TEMPLATE_VERSION
        else:
            assert (
                f"<!-- remory: template_version={PRODUCTION_TEMPLATE_VERSION} -->"
                in on_disk.decode("utf-8")
            )


def test_install_data_dir_templates_idempotent_when_stamps_match(
    tmp_path: Path,
) -> None:
    # First run writes; second run is a no-op (everything skipped as
    # unchanged).
    install_data_dir_templates(tmp_path)
    second = install_data_dir_templates(tmp_path)
    assert second.written == ()
    assert second.overwritten == ()
    # Every entry in skipped has reason="unchanged".
    assert {e.reason for e in second.skipped} == {"unchanged"}
    assert len(second.skipped) == len(_all_bundled_relpaths())


def test_install_data_dir_templates_skips_unstamped_user_modified_file_and_returns_in_skipped(
    tmp_path: Path,
) -> None:
    # Pre-stage one file *without* the stamp comment — simulates a
    # user who hand-wrote the file before refresh existed.
    target_rel = ".claude/agents/wizard.md"
    target = tmp_path / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"hand-written wizard prompt without any stamp\n")

    result = install_data_dir_templates(tmp_path)
    # The wizard file should land in skipped with the preserved reason.
    preserved = [e for e in result.skipped if e.path == target]
    assert len(preserved) == 1
    assert preserved[0].reason == "unstamped-preserved"
    # And the bytes are untouched.
    assert target.read_bytes() == b"hand-written wizard prompt without any stamp\n"


def test_install_data_dir_templates_overwrites_when_stamp_is_older_and_writes_bak(
    tmp_path: Path,
) -> None:
    target_rel = ".claude/agents/extractor.md"
    target = tmp_path / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write a stamp version 0 (older than 1).
    target.write_bytes(b"<!-- remory: template_version=0 -->\nold body\n")

    result = install_data_dir_templates(tmp_path)
    assert target in result.overwritten
    # New bytes match the bundle.
    assert target.read_bytes() == read_template_bytes(target_rel)
    # A .bak was created under .claude/.backups/. The flattened
    # filename includes the leading .claude__ since the relpath
    # flattener starts from data_dir, not from .claude/.
    backups_dir = tmp_path / ".claude" / ".backups"
    assert backups_dir.is_dir()
    baks = list(backups_dir.glob(".claude__agents__extractor.md.*.bak"))
    assert len(baks) == 1


def test_install_data_dir_templates_uses_atomic_writes_per_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Count atomic_write_bytes calls — one per bundled file at minimum.
    import remory.claude_assets as ca

    calls: list[Path] = []
    real = ca.atomic_write_bytes

    def spy(path: Path, data: bytes) -> None:
        calls.append(path)
        real(path, data)

    monkeypatch.setattr(ca, "atomic_write_bytes", spy)
    install_data_dir_templates(tmp_path)
    # At least one atomic write per bundled file (settings + 8 markdown).
    assert len(calls) >= len(_all_bundled_relpaths())
    # And every written file went through it.
    for rel in _all_bundled_relpaths():
        assert (tmp_path / rel) in calls
