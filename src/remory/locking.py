"""Topic-level advisory locking.

POSIX ``fcntl.flock(LOCK_EX | LOCK_NB)`` on a per-topic ``.lock`` file. The
lock is non-reentrant: a second acquisition of the same topic in the same
process raises :class:`LockBusyError` rather than blocking or silently
reentering. This is deliberate — re-entrancy hides nested-write bugs in
callers that assume the lock is fresh.

Windows is best-effort and is skipped at the test level. The module imports
``fcntl`` lazily so unit tests on Windows do not crash on import — but the
``topic_lock`` function will raise if invoked there.

Cleanup contract: on lock acquisition we sweep ``*.tmp`` files in the topic
directory (non-recursive) and in ``<topic>/.backups`` (if present). The
lock file itself is **not** unlinked on release; leaving it pinned to its
inode prevents a TOCTOU race with a concurrent acquirer.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from collections.abc import Generator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "LockBusyError",
    "LockError",
    "LockHandle",
    "is_locked",
    "topic_lock",
]

_log = logging.getLogger("remory.locking")


class LockError(Exception):
    """Base for locking-related errors."""


class LockBusyError(LockError):
    """The lock could not be acquired (held by another process, or in-process re-entry).

    Carries ``topic_name`` so the CLI surface can render a clean lead sentence
    without parsing the message string.
    """

    def __init__(self, message: str, *, topic_name: str | None = None) -> None:
        super().__init__(message)
        self.topic_name = topic_name


@dataclass
class LockHandle:
    path: Path
    pid: int
    acquired_at: datetime
    # Seam for the Phase 6 SessionEnd lock-wait counter; populated unconditionally
    # in Phase 1 even though nothing reads it yet. Do not delete.
    _acquire_started_at: datetime


# In-process registry of currently held locks, keyed by ``(pid, resolved_path)``.
# ``flock`` is per-fd, so a second acquire on a fresh fd in the same process
# would not naturally fail-fast on Linux; this registry enforces non-reentrancy.
_held: set[tuple[int, str]] = set()


def _on_acquire_attempt(topic_dir: Path) -> None:
    """Seam for SessionEnd lock-wait counter; do not remove."""
    # Intentionally a no-op in Phase 1.
    del topic_dir


def _cleanup_stale_tmp(topic_dir: Path) -> None:
    """Sweep ``*.tmp`` files in ``topic_dir`` and ``topic_dir/.backups``.

    Logs once at DEBUG with structured fields when at least one file is
    swept; per the spec, "silently" means no user-facing message, but the
    debug log is the audit trail.
    """
    cleaned: list[str] = []
    for tmp in topic_dir.glob("*.tmp"):
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError as exc:
                _log.debug("failed to unlink stale tmp %s: %s", tmp, exc)
                continue
            cleaned.append(str(tmp))
    backups = topic_dir / ".backups"
    if backups.is_dir():
        for tmp in backups.glob("*.tmp"):
            if tmp.is_file():
                try:
                    tmp.unlink()
                except OSError as exc:
                    _log.debug("failed to unlink stale tmp %s: %s", tmp, exc)
                    continue
                cleaned.append(str(tmp))
    if cleaned:
        _log.debug(
            "cleaned stale tmp files",
            extra={
                "topic_dir": str(topic_dir),
                "paths": cleaned,
                "count": len(cleaned),
            },
        )


def is_locked(topic_dir: Path) -> bool:
    """Non-blocking probe: returns True iff another holder has the lock."""
    if sys.platform == "win32":
        raise NotImplementedError("topic locking is POSIX-only in Phase 1")
    import fcntl

    lock_path = topic_dir / ".lock"
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        # We acquired; release immediately.
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


@contextlib.contextmanager
def _topic_lock_impl(topic_dir: Path, *, timeout: float) -> Generator[LockHandle]:
    if sys.platform == "win32":
        raise NotImplementedError("topic locking is POSIX-only in Phase 1")
    import fcntl

    _on_acquire_attempt(topic_dir)
    acquire_started_at = datetime.now(UTC)

    resolved_key = (os.getpid(), str(topic_dir.resolve()))
    if resolved_key in _held:
        raise LockBusyError(
            f"topic {topic_dir.name} is already locked in this process (non-reentrant)",
            topic_name=topic_dir.name,
        )

    lock_path = topic_dir / ".lock"
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if timeout == 0.0:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LockBusyError(
                    f"topic {topic_dir.name} is locked",
                    topic_name=topic_dir.name,
                ) from exc
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise LockBusyError(
                            f"timeout acquiring lock on {topic_dir.name}",
                            topic_name=topic_dir.name,
                        ) from exc
                    time.sleep(0.05)

        _held.add(resolved_key)
        _cleanup_stale_tmp(topic_dir)
        handle = LockHandle(
            path=lock_path,
            pid=os.getpid(),
            acquired_at=datetime.now(UTC),
            _acquire_started_at=acquire_started_at,
        )
        try:
            yield handle
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as exc:
                _log.debug("flock(LOCK_UN) raised on %s: %s", lock_path, exc)
            _held.discard(resolved_key)
    finally:
        try:
            os.close(fd)
        except OSError as exc:
            _log.debug("close(fd) raised on %s: %s", lock_path, exc)


def topic_lock(topic_dir: Path, *, timeout: float = 0.0) -> AbstractContextManager[LockHandle]:
    """Acquire an exclusive advisory lock for a topic directory.

    Args:
        topic_dir: the topic directory; the lock file is ``<topic_dir>/.lock``.
        timeout: ``0.0`` (default) means non-blocking; raise immediately on
            contention. A positive value polls every 50ms until the deadline.

    Yields:
        A :class:`LockHandle` describing the held lock.

    Raises:
        LockBusyError: if the lock is held by another process (or already
            held in this process, since the lock is non-reentrant).
    """
    return _topic_lock_impl(topic_dir, timeout=timeout)
