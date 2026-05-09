"""Top-level pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# Belt-and-suspenders: even if pytest's collection patterns drift, we don't
# want pytest trying to collect the standalone subprocess script.
collect_ignore = ["fakes/fake_claude", "fakes/lock_holder.py"]


def real_cli_available() -> tuple[bool, str]:
    """Returns (available, skip_reason). Opt-in only -- does NOT auto-detect.

    The user explicitly chose opt-in over auto-detection because
    auto-detection on PATH would silently cost contributors API calls.
    """
    if os.environ.get("REMORY_REAL_CLI") != "1":
        return False, (
            "real claude not available or REMORY_REAL_CLI not set; "
            "this test makes a real API call. Set REMORY_REAL_CLI=1 to enable."
        )
    if shutil.which("claude") is None:
        return False, "REMORY_REAL_CLI=1 set but `claude` not on PATH"
    return True, ""


@pytest.fixture
def fake_claude_path() -> Path:
    """Absolute path to the bundled fake `claude` binary."""
    return Path(__file__).parent / "fakes" / "fake_claude"


@pytest.fixture
def fake_claude_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[Path]]:
    """Put a `claude` shim (symlink to `fake_claude`) first on PATH.

    The file in the repo is named ``fake_claude`` (so it cannot be confused
    with the real binary on developer machines), but the backend looks up
    ``claude`` on PATH. This fixture creates a tmp ``bin/`` directory
    containing a ``claude`` symlink to the fake script and prepends it to
    PATH; it also isolates ``FAKE_CLAUDE_HOME`` under ``tmp_path`` so the
    fake's writes and the locator's reads agree.

    Yields ``(claude_home,)``.
    """
    fakes_dir = Path(__file__).parent / "fakes"
    fake = fakes_dir / "fake_claude"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "claude"
    shim.symlink_to(fake)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    claude_home = tmp_path / "claude_home"
    (claude_home / "projects").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FAKE_CLAUDE_HOME", str(claude_home))
    yield (claude_home,)


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
