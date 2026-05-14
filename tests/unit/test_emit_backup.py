"""Tests for :func:`remory.claude_assets.emit_backup`.

Pin the flat-path layout (``agents__wizard.md.<ts>.bak``), the UTC ISO
timestamp with hyphens, the use of the atomic-write helper, and
automatic creation of the backups directory.

See plan §11.1 + §4.4.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from remory.claude_assets import emit_backup


def test_emit_backup_writes_atomic_under_dot_claude_backups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Seed a source file inside <data_dir>/.claude/agents/.
    src = tmp_path / ".claude" / "agents" / "wizard.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"hello backup")

    # Spy on atomic_write_bytes so we confirm the write path is atomic.
    import remory.claude_assets as ca

    calls: list[Path] = []
    real = ca.atomic_write_bytes

    def spy(path: Path, data: bytes) -> None:
        calls.append(path)
        real(path, data)

    monkeypatch.setattr(ca, "atomic_write_bytes", spy)
    bak_path = emit_backup(src, tmp_path)
    # The .bak is somewhere under .claude/.backups/.
    expected_backups_dir = tmp_path / ".claude" / ".backups"
    assert bak_path.parent == expected_backups_dir
    assert bak_path.is_file()
    assert bak_path.read_bytes() == b"hello backup"
    # And the atomic helper was used.
    assert bak_path in calls


def test_emit_backup_path_uses_flattened_slashes(tmp_path: Path) -> None:
    src = tmp_path / ".claude" / "agents" / "wizard.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    bak_path = emit_backup(src, tmp_path)
    # Filename starts with the flattened relative path
    # `.claude__agents__wizard.md`. The trailing-after-the-last-component
    # bit is `.<ts>.bak`, so we assert the prefix.
    assert bak_path.name.startswith(".claude__agents__wizard.md.")
    # And the flattened bit must not contain raw OS separators.
    assert "/" not in bak_path.name
    assert "\\" not in bak_path.name


def test_emit_backup_path_uses_utc_iso_timestamp_with_colons_replaced(
    tmp_path: Path,
) -> None:
    src = tmp_path / ".claude" / "agents" / "wizard.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    bak_path = emit_backup(src, tmp_path)
    # Match `<flat>.YYYY-MM-DDTHH-MM-SSZ.bak` — no `:` anywhere in the name.
    assert ":" not in bak_path.name
    ts_match = re.search(r"\.(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)\.bak$", bak_path.name)
    assert ts_match is not None, f"expected UTC-ISO-with-hyphens timestamp, got {bak_path.name!r}"


def test_emit_backup_creates_backups_dir_if_missing(tmp_path: Path) -> None:
    src = tmp_path / ".claude" / "agents" / "wizard.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"y")
    backups_dir = tmp_path / ".claude" / ".backups"
    assert not backups_dir.exists()
    bak_path = emit_backup(src, tmp_path)
    assert backups_dir.is_dir()
    assert bak_path.parent == backups_dir
