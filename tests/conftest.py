"""Top-level pytest configuration and shared fixtures."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# Belt-and-suspenders: even if pytest's collection patterns drift, we don't
# want pytest trying to collect the standalone subprocess script.
collect_ignore = ["fakes"]


def _cleanup_holder(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired as e:
                    raise RuntimeError(
                        f"lock_holder subprocess (pid={proc.pid}) survived SIGKILL; "
                        "CI must fail loudly rather than leak"
                    ) from e
    assert proc.poll() is not None, "lock_holder subprocess leaked"


@pytest.fixture
def multi_process_lock_holder() -> Iterator[Callable[[Path], subprocess.Popen[str]]]:
    """Spawn a subprocess that acquires topic_lock on a given topic dir.

    Subprocess leaks make CI flaky; flaky CI erodes trust in the suite.
    Failures here fail the test loudly rather than silently linger.
    """
    spawned: list[subprocess.Popen[str]] = []
    holder_script = Path(__file__).parent / "fakes" / "lock_holder.py"

    def factory(topic_dir: Path) -> subprocess.Popen[str]:
        proc = subprocess.Popen(
            [sys.executable, str(holder_script), str(topic_dir)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        spawned.append(proc)
        # Wait for "LOCKED\n" from the child so the caller knows the lock
        # has actually been acquired before doing anything contention-sensitive.
        assert proc.stdout is not None
        line = proc.stdout.readline()
        if line != "LOCKED\n":
            # The child failed to lock; capture diagnostics and bail.
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
            stderr = ""
            if proc.stderr is not None:
                stderr = proc.stderr.read()
            raise RuntimeError(
                f"lock_holder did not signal LOCKED (got {line!r}); stderr={stderr!r}"
            )
        return proc

    try:
        yield factory
    finally:
        for proc in spawned:
            _cleanup_holder(proc)
