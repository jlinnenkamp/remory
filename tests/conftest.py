"""Top-level pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remory.locking import topic_lock
from remory.raw import RawFrontmatter, RawSource, RawStatus, write_raw
from remory.schema import load_builtin
from remory.state import StateDoc, StateFrontmatter, StateSection, write_state
from remory.topic import Knobs, TopicMeta, write_meta

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


# ---------------------------------------------------------------------------
# Sleep-pipeline-shared fixtures (Phase 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeededTopic:
    """Result of the :data:`seeded_topic` fixture.

    Carries the topic directory, the schema name, and the list of pending
    raw entry paths in the order they were written. Tests that need finer
    control can read the files back via :func:`remory.raw.read_raw`.
    """

    topic_dir: Path
    schema_name: str
    pending_paths: tuple[Path, ...]


def _seed_topic(
    *,
    base_dir: Path,
    schema_name: str,
    pending_count: int,
    seed_state: bool,
    knobs: Knobs | None = None,
) -> SeededTopic:
    schema = load_builtin(schema_name)
    topic_dir = base_dir / schema_name
    topic_dir.mkdir(parents=True, exist_ok=True)
    effective_knobs = knobs or Knobs(
        tone=schema.defaults.tone, strictness=schema.defaults.strictness
    )
    meta = TopicMeta(
        schema=schema_name,
        schema_version=schema.version,
        created=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        last_consolidated=None,
        last_chat=None,
        pending_count=pending_count,
        total_entries=pending_count,
        knobs=effective_knobs,
    )
    pending_paths: list[Path] = []
    with topic_lock(topic_dir):
        write_meta(topic_dir, meta)
        if seed_state:
            doc = StateDoc(
                frontmatter=StateFrontmatter(
                    schema=schema_name,
                    schema_version=schema.version,
                    last_consolidated=None,
                    entries_consolidated=0,
                ),
                sections=[StateSection(title=s.title, body="\n") for s in schema.sections],
            )
            write_state(topic_dir / "state.md", doc)
        # Pending raw entries with deterministic, ascending timestamps.
        base_when = datetime(2026, 5, 9, 9, 0, tzinfo=UTC)
        for i in range(pending_count):
            when = base_when + timedelta(minutes=i * 10)
            fm = RawFrontmatter(
                created=when,
                source=RawSource.CHAT,
                status=RawStatus.PENDING,
                session_id=f"sess-{i:03d}",
            )
            pending_paths.append(write_raw(topic_dir, frontmatter=fm, body=f"raw entry {i}"))
    return SeededTopic(
        topic_dir=topic_dir,
        schema_name=schema_name,
        pending_paths=tuple(pending_paths),
    )


@pytest.fixture
def seeded_topic_factory(
    tmp_path: Path,
) -> Callable[..., SeededTopic]:
    """Factory fixture: build a topic dir with meta.yaml, state.md, and N pending raws.

    Parameters: ``schema_name`` (default "job-profile"), ``pending_count``
    (default 2), ``seed_state`` (default True), ``knobs`` (default schema
    defaults). Returns a :class:`SeededTopic`.
    """

    def factory(
        *,
        schema_name: str = "job-profile",
        pending_count: int = 2,
        seed_state: bool = True,
        knobs: Knobs | None = None,
    ) -> SeededTopic:
        return _seed_topic(
            base_dir=tmp_path,
            schema_name=schema_name,
            pending_count=pending_count,
            seed_state=seed_state,
            knobs=knobs,
        )

    return factory


@pytest.fixture
def seeded_topic(seeded_topic_factory: Callable[..., SeededTopic]) -> SeededTopic:
    """Default seeded topic: job-profile, 2 pending raws, seeded state.md."""
    return seeded_topic_factory()
