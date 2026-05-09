"""Unit tests for ``remory.atomic``."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from remory.atomic import atomic_write_bytes, atomic_write_text


def test_atomic_write_text_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "hello.txt"
    atomic_write_text(target, "hello world")
    assert target.read_text(encoding="utf-8") == "hello world"
    # No leftover .tmp.
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_atomic_write_bytes_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    payload = b"\x00\x01\x02deadbeef"
    atomic_write_bytes(target, payload)
    assert target.read_bytes() == payload
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_atomic_write_text_rollback_on_replace_failure_leaves_target_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.md"
    target.write_text("ORIGINAL", encoding="utf-8")

    def boom(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        # Sanity-check the temp file got written before we sabotage replace.
        assert Path(src).exists()
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_text(target, "NEW CONTENT")

    # Original content untouched.
    assert target.read_text(encoding="utf-8") == "ORIGINAL"
    # Temp file cleaned up.
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"unexpected leftover: {leftovers}"


def test_atomic_write_text_creates_target_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "fresh.md"
    assert not target.exists()
    atomic_write_text(target, "first write")
    assert target.read_text(encoding="utf-8") == "first write"


def test_atomic_write_text_swallows_parent_dir_fsync_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If parent-dir fsync raises OSError, the call still succeeds.

    We monkeypatch ``os.fsync`` to raise only when called on a directory fd
    (i.e. a fd that is not the file we just wrote). The data file's fsync
    must continue to work.
    """
    target = tmp_path / "x.txt"

    real_fsync = os.fsync

    def selective_fsync(fd: int) -> None:
        try:
            stat = os.fstat(fd)
        except OSError:
            real_fsync(fd)
            return
        # S_IFDIR check: raise on directory fds, pass through on regular files.
        import stat as _stat

        if _stat.S_ISDIR(stat.st_mode):
            raise OSError("simulated parent-dir fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", selective_fsync)

    with caplog.at_level(logging.DEBUG, logger="remory.atomic"):
        atomic_write_text(target, "ok")

    assert target.read_text(encoding="utf-8") == "ok"
    # Should have logged a DEBUG record from remory.atomic about the parent-dir fsync.
    matching = [
        r for r in caplog.records if r.name == "remory.atomic" and r.levelno == logging.DEBUG
    ]
    assert matching, "expected a DEBUG log from remory.atomic on parent-dir fsync OSError"
