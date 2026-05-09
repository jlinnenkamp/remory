"""Atomic file writes.

Pure atomicity primitive: write a sibling ``.tmp``, fsync the file, rename
into place, best-effort fsync the parent directory. No backup logic, no
domain knowledge. Backups are sleep's concern (Phase 3).

See ``docs/adr/0001-fsync-on-darwin.md`` for the rationale on using only
``os.fsync`` and not ``F_FULLFSYNC`` on macOS.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

__all__ = ["atomic_write_bytes", "atomic_write_text"]

_log = logging.getLogger("remory.atomic")


def _tmp_path(path: Path) -> Path:
    """Return a sibling temp path. Same directory so ``os.replace`` is atomic.

    Multi-suffix paths (``foo.bar.baz``) only get ``.tmp`` appended to the
    last suffix; the temp filename does not need to be canonical, only to
    be in the same directory as ``path``.
    """
    return path.with_suffix(path.suffix + ".tmp")


def _fsync_parent_dir(path: Path) -> None:
    """Best-effort fsync on the parent directory's fd.

    POSIX-only. On Windows (where opening a directory for fsync is not
    supported) we swallow ``OSError`` and log at DEBUG; the rename has
    already been issued. We also swallow on POSIX in the unlikely event
    the open or fsync fails, since the data file's fsync + rename is the
    durability bedrock; the parent-dir fsync is belt-and-suspenders.
    """
    if sys.platform == "win32":
        _log.debug("skipping parent-dir fsync on win32 for %s", path)
        return
    parent = path.parent
    try:
        fd = os.open(str(parent), os.O_RDONLY)
    except OSError as exc:
        _log.debug("could not open parent dir for fsync: %s (%s)", parent, exc)
        return
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            _log.debug("parent-dir fsync raised OSError on %s: %s", parent, exc)
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path``.

    On any failure during write/fsync/replace, the temp file is cleaned up
    and the original target is left untouched.
    """
    tmp = _tmp_path(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_parent_dir(path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            _log.debug("could not clean up temp file %s: %s", tmp, exc)
        raise


def atomic_write_text(path: Path, data: str, *, encoding: str = "utf-8") -> None:
    """Atomically write ``data`` to ``path`` as text in ``encoding``.

    Implementation note: we open in text mode so the encoding round-trip
    matches what callers expect from a string write; durability story is
    identical to :func:`atomic_write_bytes`.
    """
    tmp = _tmp_path(path)
    try:
        with open(tmp, "w", encoding=encoding) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_parent_dir(path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            _log.debug("could not clean up temp file %s: %s", tmp, exc)
        raise
