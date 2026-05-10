"""Integration tests for the sleep pipeline against the fake `claude` binary.

These tests exercise the real subprocess seam (``ClaudeCodeBackend`` ->
``fake_claude``) while seeding a topic on disk and asserting end-to-end
state.md / meta.yaml / _review.md / .backups outcomes.

The fake binary's "scripted" mode is used to return a per-call sequence of
canned responses; the counter file persists across re-execs so successive
sleep stages see distinct outputs.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from remory import paths
from remory.backends.claude_code import ClaudeCodeBackend
from remory.raw import RawStatus, list_raw
from remory.sleep import SleepError, SleepStatus, sleep
from remory.state import read_state
from remory.topic import read_meta
from tests.conftest import SeededTopic

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fake binary")


def _setup_scripted_fake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    responses: list[str],
) -> tuple[Path, Path]:
    """Configure the fake binary's scripted mode for one test.

    Returns ``(script_path, counter_path)`` for tests that want to assert on
    them. The fake claude binary must already be on PATH (use the
    ``fake_claude_on_path`` fixture).
    """
    script_path = tmp_path / "script.json"
    counter_path = tmp_path / "counter.txt"
    script_path.write_text(json.dumps(responses), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "scripted")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT_FILE", str(script_path))
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT_COUNTER_FILE", str(counter_path))
    return script_path, counter_path


def _job_profile_payload(seeded: SeededTopic) -> str:
    p0 = seeded.pending_paths[0]
    rel0 = f"raw/{p0.parent.name}/{p0.name}"
    return json.dumps(
        {
            "skills_and_strengths": [{"text": "deep-focus work", "evidence": rel0}],
            "evidence_log": [{"text": "Logged insight", "evidence": rel0}],
        }
    )


def test_sleep_flow_job_profile_merge_and_critique_full_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_claude_on_path: tuple[Path],
    seeded_topic_factory: Callable[..., SeededTopic],
) -> None:
    del fake_claude_on_path
    seeded = seeded_topic_factory(schema_name="job-profile", pending_count=2)
    payload = _job_profile_payload(seeded)
    # job-profile is merge_and_critique. Calls expected:
    # 1. extract -> JSON payload
    # 2. merge(skills_and_strengths) draft
    # 3. merge(skills_and_strengths) revise
    # 4. critique
    # (hard_constraints / values / etc. have no candidates -> skipped;
    #  evidence_log is append_only -> mechanical; no LLM calls for those.)
    _setup_scripted_fake(
        monkeypatch,
        tmp_path,
        [
            payload,
            "(skills) draft body\n",
            "(skills) revised body\n",
            "# Review\n\nLooks consistent.\n",
        ],
    )
    backend = ClaudeCodeBackend()
    result = sleep(topic_dir=seeded.topic_dir, backend=backend)

    assert result.status is SleepStatus.SUCCESS
    assert result.consolidated_count == 2
    # Backup made.
    assert result.backup_path is not None
    assert result.backup_path.exists()
    # Review written.
    assert result.review_path is not None
    review_text = result.review_path.read_text(encoding="utf-8")
    assert "Looks consistent." in review_text
    # state.md updated.
    state = read_state(paths.state_file(seeded.topic_dir))
    assert state.frontmatter.entries_consolidated == 2
    skills_body = next(s for s in state.sections if s.title == "Skills and strengths").body
    assert "(skills) revised body" in skills_body
    # evidence_log got the append-only bullet.
    evidence_body = next(s for s in state.sections if s.title == "Evidence log").body
    assert "Logged insight" in evidence_body
    assert "(evidence: raw/2026/" in evidence_body
    # Raw entries flipped to consolidated.
    assert list_raw(seeded.topic_dir, status=RawStatus.PENDING) == []
    assert len(list_raw(seeded.topic_dir, status=RawStatus.CONSOLIDATED)) == 2
    # meta.yaml updated.
    meta = read_meta(seeded.topic_dir)
    assert meta.pending_count == 0
    assert meta.last_consolidated is not None


def test_sleep_flow_workout_single_pass_no_review_md(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_claude_on_path: tuple[Path],
    seeded_topic_factory: Callable[..., SeededTopic],
) -> None:
    del fake_claude_on_path
    seeded = seeded_topic_factory(schema_name="workout", pending_count=1)
    p0 = seeded.pending_paths[0]
    rel0 = f"raw/{p0.parent.name}/{p0.name}"
    payload = json.dumps({"current_plan": [{"text": "more squats", "evidence": rel0}]})
    # workout is single_pass: extract + 1 merge call (no revise).
    _setup_scripted_fake(monkeypatch, tmp_path, [payload, "merged plan body\n"])

    backend = ClaudeCodeBackend()
    result = sleep(topic_dir=seeded.topic_dir, backend=backend)
    assert result.status is SleepStatus.SUCCESS
    assert result.review_path is None
    assert not paths.review_file(seeded.topic_dir).exists()
    state = read_state(paths.state_file(seeded.topic_dir))
    plan = next(s for s in state.sections if s.title == "Current plan").body
    assert "merged plan body" in plan


def test_sleep_flow_no_pending_no_op(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_claude_on_path: tuple[Path],
    seeded_topic_factory: Callable[..., SeededTopic],
) -> None:
    del fake_claude_on_path
    seeded = seeded_topic_factory(schema_name="job-profile", pending_count=0)
    # Even with the fake configured, no calls should fire.
    _setup_scripted_fake(monkeypatch, tmp_path, [])
    state_before = paths.state_file(seeded.topic_dir).read_bytes()
    meta_before = read_meta(seeded.topic_dir)
    backend = ClaudeCodeBackend()
    result = sleep(topic_dir=seeded.topic_dir, backend=backend)
    assert result.status is SleepStatus.NO_PENDING
    assert paths.state_file(seeded.topic_dir).read_bytes() == state_before
    assert read_meta(seeded.topic_dir) == meta_before


def test_sleep_flow_extract_failure_keeps_topic_pristine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_claude_on_path: tuple[Path],
    seeded_topic_factory: Callable[..., SeededTopic],
) -> None:
    del fake_claude_on_path
    seeded = seeded_topic_factory(schema_name="job-profile", pending_count=1)
    # Both extract attempts (initial + stricter) return malformed text.
    _setup_scripted_fake(monkeypatch, tmp_path, ["not json", "still not json"])
    state_before = paths.state_file(seeded.topic_dir).read_bytes()
    meta_before = read_meta(seeded.topic_dir)

    backend = ClaudeCodeBackend()
    with pytest.raises(SleepError) as excinfo:
        sleep(topic_dir=seeded.topic_dir, backend=backend)
    assert excinfo.value.stage == "extract"
    assert excinfo.value.backup_path is None
    # Topic is pristine: state, meta, raws untouched.
    assert paths.state_file(seeded.topic_dir).read_bytes() == state_before
    assert read_meta(seeded.topic_dir) == meta_before
    assert len(list_raw(seeded.topic_dir, status=RawStatus.PENDING)) == 1
    # No backups directory or no backups in it.
    assert not paths.backups_dir(seeded.topic_dir).exists() or not list(
        paths.backups_dir(seeded.topic_dir).glob("*.bak")
    )


def test_sleep_flow_merge_failure_after_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_claude_on_path: tuple[Path],
    seeded_topic_factory: Callable[..., SeededTopic],
) -> None:
    """T3: backup is recoverable -- bytes match the pre-sleep state.md exactly."""
    del fake_claude_on_path
    seeded = seeded_topic_factory(schema_name="job-profile", pending_count=1)
    state_path = paths.state_file(seeded.topic_dir)
    pre_sleep_state_md_bytes = state_path.read_bytes()

    p0 = seeded.pending_paths[0]
    rel0 = f"raw/{p0.parent.name}/{p0.name}"
    extract_payload = json.dumps({"skills_and_strengths": [{"text": "x", "evidence": rel0}]})

    # extract succeeds; merge call fails. We need merge to fail repeatably
    # (3 attempts of tenacity-retried invocation errors) so the easiest is
    # to have the fake exit non-zero. We do that by *ending the script* --
    # the fake exits 2 once exhausted, which is a non-zero exit and surfaces
    # as ``BackendInvocationError``.
    _setup_scripted_fake(monkeypatch, tmp_path, [extract_payload])

    backend = ClaudeCodeBackend()
    with pytest.raises(SleepError) as excinfo:
        sleep(topic_dir=seeded.topic_dir, backend=backend)
    err = excinfo.value
    assert err.stage == "merge"
    assert err.backup_path is not None
    assert err.backup_path.exists()
    # T3: backup is recoverable, not just present.
    assert err.backup_path.read_bytes() == pre_sleep_state_md_bytes
    # state.md unchanged.
    assert state_path.read_bytes() == pre_sleep_state_md_bytes
    # Raws still pending; meta unchanged.
    assert len(list_raw(seeded.topic_dir, status=RawStatus.PENDING)) == 1


def test_sleep_flow_dry_run_writes_nothing_returns_proposed_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_claude_on_path: tuple[Path],
    seeded_topic_factory: Callable[..., SeededTopic],
) -> None:
    del fake_claude_on_path
    seeded = seeded_topic_factory(schema_name="workout", pending_count=1)
    p0 = seeded.pending_paths[0]
    rel0 = f"raw/{p0.parent.name}/{p0.name}"
    payload = json.dumps({"current_plan": [{"text": "more squats", "evidence": rel0}]})
    _setup_scripted_fake(monkeypatch, tmp_path, [payload, "merged plan body\n"])
    state_before = paths.state_file(seeded.topic_dir).read_bytes()

    backend = ClaudeCodeBackend()
    result = sleep(topic_dir=seeded.topic_dir, backend=backend, dry_run=True)
    # No on-disk change.
    assert paths.state_file(seeded.topic_dir).read_bytes() == state_before
    assert not paths.backups_dir(seeded.topic_dir).exists() or not list(
        paths.backups_dir(seeded.topic_dir).glob("*.bak")
    )
    # Proposed text is in notes.
    assert any("merged plan body" in n for n in result.notes)
    assert any("DRY-RUN" in n for n in result.notes)
