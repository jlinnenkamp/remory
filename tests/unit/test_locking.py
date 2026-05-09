"""Unit tests for ``remory.locking``.

Cross-process tests use the ``multi_process_lock_holder`` fixture defined in
``tests/conftest.py`` — a real subprocess is the only honest way to test
``flock`` semantics; threads share fds and would lie.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from remory.locking import (
    LockBusyError,
    is_locked,
    topic_lock,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock only")


def _make_topic(tmp_path: Path) -> Path:
    """Create a topic dir with a backups subdir and return the path."""
    d = tmp_path / "topic"
    d.mkdir()
    return d


def test_topic_lock_basic_acquire_succeeds_and_lock_released_after_block(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    with topic_lock(d) as handle:
        assert handle.path == d / ".lock"
        assert handle.pid == os.getpid()
    # ``flock`` is per-OFD: a fresh probe fd inside the holder process would
    # see the lock as held, so we cannot meaningfully assert ``is_locked``
    # is False *while* still holding. We can only assert it after release.
    assert is_locked(d) is False


def test_topic_lock_inproc_reentry_raises_LockBusyError(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    with topic_lock(d), pytest.raises(LockBusyError), topic_lock(d):
        pass


def test_topic_lock_blocks_cross_process_until_release(
    tmp_path: Path,
    multi_process_lock_holder: Callable[[Path], subprocess.Popen[str]],
) -> None:
    d = _make_topic(tmp_path)
    holder = multi_process_lock_holder(d)
    start = time.monotonic()
    try:
        with pytest.raises(LockBusyError), topic_lock(d, timeout=0.0):
            pass
        assert time.monotonic() - start < 5.0, "non-blocking acquire took too long"

        # Release the holder by closing its stdin; it exits cleanly.
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=5.0)

        # Now main process can acquire.
        with topic_lock(d) as h:
            assert h.pid == os.getpid()
    finally:
        # Fixture cleanup will also handle this, but make doubly sure we
        # don't leave the subprocess wedged.
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=2.0)


def test_topic_lock_timeout_expires_when_held_by_other_process(
    tmp_path: Path,
    multi_process_lock_holder: Callable[[Path], subprocess.Popen[str]],
) -> None:
    d = _make_topic(tmp_path)
    multi_process_lock_holder(d)
    start = time.monotonic()
    with pytest.raises(LockBusyError), topic_lock(d, timeout=0.5):
        pass
    elapsed = time.monotonic() - start
    # ~0.5s; allow generous slack for slow CI but keep the upper bound tight
    # enough to flag a runaway loop.
    assert 0.4 <= elapsed < 3.0, f"expected ~0.5s, got {elapsed:.3f}s"


def test_is_locked_returns_true_while_other_process_holds(
    tmp_path: Path,
    multi_process_lock_holder: Callable[[Path], subprocess.Popen[str]],
) -> None:
    d = _make_topic(tmp_path)
    holder = multi_process_lock_holder(d)
    try:
        assert is_locked(d) is True
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=5.0)
    assert is_locked(d) is False


def test_topic_lock_cleans_stale_tmp_and_logs_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    d = _make_topic(tmp_path)
    backups = d / ".backups"
    backups.mkdir()
    stale_a = d / "state.md.tmp"
    stale_a.write_text("partial", encoding="utf-8")
    stale_b = backups / "state.md.2026-05-09-1820.bak.tmp"
    stale_b.write_text("partial", encoding="utf-8")

    with caplog.at_level(logging.DEBUG, logger="remory.locking"), topic_lock(d):
        pass

    assert not stale_a.exists()
    assert not stale_b.exists()

    cleanup_records = [r for r in caplog.records if r.message == "cleaned stale tmp files"]
    assert len(cleanup_records) == 1
    rec = cleanup_records[0]
    assert rec.levelno == logging.DEBUG
    assert rec.name == "remory.locking"
    # Structured fields land on the LogRecord via ``extra=``; access via vars()
    # since they are not declared on LogRecord.
    rec_vars = vars(rec)
    assert rec_vars["count"] == 2
    paths_field = rec_vars["paths"]
    assert str(stale_a) in paths_field
    assert str(stale_b) in paths_field


def test_topic_lock_does_not_log_when_no_stale_tmp(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    d = _make_topic(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="remory.locking"), topic_lock(d):
        pass
    cleanup_records = [r for r in caplog.records if r.message == "cleaned stale tmp files"]
    assert cleanup_records == []


def test_topic_lock_release_does_not_unlink_lockfile(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    with topic_lock(d):
        pass
    # TOCTOU avoidance: the .lock file persists between acquisitions.
    assert (d / ".lock").exists()


def test_topic_lock_survives_sigkill_of_holder(
    tmp_path: Path,
    multi_process_lock_holder: Callable[[Path], subprocess.Popen[str]],
) -> None:
    d = _make_topic(tmp_path)
    holder = multi_process_lock_holder(d)
    os.kill(holder.pid, signal.SIGKILL)
    holder.wait(timeout=5.0)
    # After SIGKILL the kernel releases the flock; main process can acquire immediately.
    with topic_lock(d) as h:
        assert h.pid == os.getpid()
