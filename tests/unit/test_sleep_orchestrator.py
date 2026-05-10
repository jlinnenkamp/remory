"""Unit tests for ``remory.sleep.orchestrator``."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remory import paths
from remory.backends.base import HeadlessMeta, HeadlessResult
from remory.locking import LockBusyError, topic_lock
from remory.raw import RawStatus, list_raw
from remory.schema import load_builtin
from remory.sleep import (
    SectionOutcome,
    SleepError,
    SleepResult,
    SleepStatus,
    sleep,
)
from remory.state import (
    StateDoc,
    StateFrontmatter,
    StateSection,
    read_state,
    write_state,
)
from remory.topic import read_meta
from tests.conftest import SeededTopic
from tests.fakes.fake_backend import FakeBackend

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock only")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(text: str) -> HeadlessResult:
    return HeadlessResult(
        text=text,
        session_id="s",
        duration_ms=1,
        num_turns=1,
        stop_reason="end_turn",
        meta=HeadlessMeta(raw_envelope=None),
    )


def _job_profile_extract_payload(seeded: SeededTopic) -> str:
    """Build an extract payload that targets all candidate-receiving sections.

    Uses the first pending raw as evidence for ``skills_and_strengths`` and
    ``hard_constraints`` (LLM-merged), and the second pending raw for
    ``evidence_log`` (append-only).
    """
    paths_list: list[Path] = list(seeded.pending_paths)
    assert paths_list, "_job_profile_extract_payload requires at least 1 pending"
    p0 = paths_list[0]
    p1 = paths_list[1] if len(paths_list) > 1 else paths_list[0]
    rel0 = f"raw/{p0.parent.name}/{p0.name}"
    rel1 = f"raw/{p1.parent.name}/{p1.name}"
    return json.dumps(
        {
            "skills_and_strengths": [{"text": "solo deep-focus work", "evidence": rel0}],
            "hard_constraints": [{"text": "no relocation 2y", "evidence": rel0}],
            "evidence_log": [{"text": "Logged insight", "evidence": rel1}],
        }
    )


def _all_responses_for_full_pipeline(seeded: SeededTopic) -> list[HeadlessResult]:
    """Canned responses for a job-profile (merge_and_critique) sleep run.

    Order: extract, merge(skills) draft, merge(skills) revise, merge(hard) draft,
    merge(hard) revise, critique. evidence_log is append-only -- no LLM call.
    """
    return [
        _result(_job_profile_extract_payload(seeded)),
        _result("(skills) drafted body\n"),
        _result("(skills) revised body\n"),
        _result("(constraints) drafted body\n"),
        _result("(constraints) revised body\n"),
        _result("# Review\n\nLooks consistent.\n"),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sleep_no_pending_returns_no_pending_no_backup_no_meta_change(
    seeded_topic_factory: Callable[..., SeededTopic],
) -> None:
    seeded = seeded_topic_factory(pending_count=0)
    backend = FakeBackend(headless_results=[])
    meta_before = read_meta(seeded.topic_dir)

    result = sleep(topic_dir=seeded.topic_dir, backend=backend)

    assert isinstance(result, SleepResult)
    assert result.status is SleepStatus.NO_PENDING
    assert result.backup_path is None
    assert result.consolidated_count == 0
    # Backend was not invoked.
    assert backend.headless_calls == []
    # Meta unchanged.
    assert read_meta(seeded.topic_dir) == meta_before
    # No backup produced.
    assert not paths.backups_dir(seeded.topic_dir).exists() or not list(
        paths.backups_dir(seeded.topic_dir).glob("*.bak")
    )


def test_sleep_takes_backup_before_attempting_merge(
    seeded_topic: SeededTopic,
) -> None:
    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))
    result = sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    assert result.backup_path is not None
    assert result.backup_path.exists()
    backups = list(paths.backups_dir(seeded_topic.topic_dir).glob("*.bak"))
    assert len(backups) == 1


def test_sleep_backup_uses_atomic_write_bytes_not_shutil_copy(
    seeded_topic: SeededTopic,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinned by C5: backup goes through ``atomic_write_bytes``, not ``shutil.copy``."""
    calls: list[tuple[Path, bytes]] = []

    from remory.sleep import orchestrator as orch

    real = orch.atomic_write_bytes

    def spy(path: Path, data: bytes) -> None:
        calls.append((path, data))
        real(path, data)

    monkeypatch.setattr(orch, "atomic_write_bytes", spy)

    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))
    sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    assert len(calls) == 1
    backup_path, _ = calls[0]
    assert backup_path.name.endswith(".bak")
    assert ".backups" in backup_path.parts


def test_sleep_acquires_topic_lock_for_full_pipeline(
    seeded_topic: SeededTopic,
    multi_process_lock_holder: Callable[[Path], object],
) -> None:
    """If another process holds the lock, sleep with default lock_timeout=0 raises."""
    multi_process_lock_holder(seeded_topic.topic_dir)
    backend = FakeBackend(headless_results=[])
    with pytest.raises(LockBusyError):
        sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    # Backend was never invoked because we never got past the lock.
    assert backend.headless_calls == []


def test_sleep_skips_llm_for_append_only_sections(
    seeded_topic: SeededTopic,
) -> None:
    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))
    sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    # 6 calls total: extract + 2*(merge+revise) + critique. Append-only is mechanical.
    assert len(backend.headless_calls) == 6
    agents = [call["agent"] for call in backend.headless_calls]
    # No "merger" call has an evidence_log section in its prompt (we already
    # tested isolation in prompts; here we assert the section was not given
    # to the merger at all).
    for call, agent in zip(backend.headless_calls, agents, strict=True):
        if agent == "merger":
            prompt = call["prompt"]
            assert isinstance(prompt, str)
            assert "Evidence log" not in prompt


def test_sleep_skips_merge_call_for_section_with_no_candidates(
    seeded_topic: SeededTopic,
) -> None:
    """If extract returns candidates only for a subset of sections, only those merge."""
    p0 = seeded_topic.pending_paths[0]
    rel0 = f"raw/{p0.parent.name}/{p0.name}"
    payload = json.dumps(
        {
            "skills_and_strengths": [{"text": "x", "evidence": rel0}],
        }
    )
    # extract + 2 merge calls (draft + revise) for skills_and_strengths only +
    # critique. No call for any other section.
    backend = FakeBackend(
        headless_results=[
            _result(payload),
            _result("draft\n"),
            _result("revised\n"),
            _result("# Review\n\nok\n"),
        ]
    )
    result = sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    assert len(backend.headless_calls) == 4
    actions = {o.section_id: o.action for o in result.section_outcomes}
    assert actions["skills_and_strengths"] == "llm_merge"
    assert actions["hard_constraints"] == "skipped_no_candidates"
    assert actions["evidence_log"] == "skipped_no_candidates"


def test_sleep_single_pass_does_not_write_review_md(
    seeded_topic_factory: Callable[..., SeededTopic],
) -> None:
    """Workout schema is single_pass; no critique, no _review.md."""
    seeded = seeded_topic_factory(schema_name="workout", pending_count=1)
    p0 = seeded.pending_paths[0]
    rel0 = f"raw/{p0.parent.name}/{p0.name}"
    payload = json.dumps(
        {
            "current_plan": [{"text": "more squats", "evidence": rel0}],
        }
    )
    backend = FakeBackend(
        headless_results=[
            _result(payload),
            _result("merged plan\n"),
        ]
    )
    result = sleep(topic_dir=seeded.topic_dir, backend=backend)
    assert result.review_path is None
    assert not paths.review_file(seeded.topic_dir).exists()
    # Only 2 calls: extract + 1 merge (no revise on single_pass).
    assert len(backend.headless_calls) == 2


def test_sleep_merge_and_critique_writes_review_md(
    seeded_topic: SeededTopic,
) -> None:
    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))
    result = sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    assert result.review_path is not None
    assert result.review_path.exists()
    assert "Looks consistent." in result.review_path.read_text(encoding="utf-8")


def test_sleep_critique_failure_returns_success_with_warnings_no_review_md_state_md_still_written(
    seeded_topic: SeededTopic,
) -> None:
    """D2: critique failure is non-fatal; state.md still written; status warns."""
    extract = _result(_job_profile_extract_payload(seeded_topic))
    backend = FakeBackend(
        headless_results=[
            extract,
            _result("(skills) draft\n"),
            _result("(skills) revised\n"),
            _result("(constraints) draft\n"),
            _result("(constraints) revised\n"),
            _result("   \n"),  # critique returns whitespace -> CritiqueError
        ]
    )
    result = sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    assert result.status is SleepStatus.SUCCESS_WITH_WARNINGS
    assert result.review_path is None
    assert not paths.review_file(seeded_topic.topic_dir).exists()
    # state.md was still written.
    state = read_state(paths.state_file(seeded_topic.topic_dir))
    assert state.frontmatter.entries_consolidated == 2
    # A note carries the failure message.
    assert any("critique failed" in n for n in result.notes)


def test_sleep_extract_failure_raises_sleep_error_no_backup_raw_entries_still_pending(
    seeded_topic: SeededTopic,
) -> None:
    """D5: extract failure aborts before backup; raws untouched."""
    backend = FakeBackend(
        headless_results=[
            _result("malformed garbage 1"),
            _result("malformed garbage 2"),
        ]
    )
    with pytest.raises(SleepError) as excinfo:
        sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    err = excinfo.value
    assert err.stage == "extract"
    assert err.backup_path is None
    # No backup written.
    assert not paths.backups_dir(seeded_topic.topic_dir).exists() or not list(
        paths.backups_dir(seeded_topic.topic_dir).glob("*.bak")
    )
    # Raw entries still pending.
    pending_after = list_raw(seeded_topic.topic_dir, status=RawStatus.PENDING)
    assert len(pending_after) == 2


def test_sleep_merge_failure_raises_sleep_error_with_backup_path_state_path_stage_cause(
    seeded_topic: SeededTopic,
) -> None:
    from remory.backends.base import BackendInvocationError

    backend = FakeBackend(
        headless_results=[
            _result(_job_profile_extract_payload(seeded_topic)),
            BackendInvocationError("boom 1", exit_code=1),
            BackendInvocationError("boom 2", exit_code=1),
            BackendInvocationError("boom 3", exit_code=1),
        ]
    )
    with pytest.raises(SleepError) as excinfo:
        sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    err = excinfo.value
    assert err.stage == "merge"
    assert err.backup_path is not None
    assert err.backup_path.exists()
    assert err.state_path == paths.state_file(seeded_topic.topic_dir)
    assert err.cause is not None


def test_sleep_merge_failure_leaves_state_md_unchanged_raws_still_pending_meta_unchanged(
    seeded_topic: SeededTopic,
) -> None:
    from remory.backends.base import BackendInvocationError

    state_path = paths.state_file(seeded_topic.topic_dir)
    state_before = state_path.read_bytes()
    meta_before = read_meta(seeded_topic.topic_dir)

    backend = FakeBackend(
        headless_results=[
            _result(_job_profile_extract_payload(seeded_topic)),
            BackendInvocationError("b", exit_code=1),
            BackendInvocationError("b", exit_code=1),
            BackendInvocationError("b", exit_code=1),
        ]
    )
    with pytest.raises(SleepError):
        sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    # state.md unchanged.
    assert state_path.read_bytes() == state_before
    # meta.yaml unchanged.
    assert read_meta(seeded_topic.topic_dir) == meta_before
    # raws still pending.
    pending_after = list_raw(seeded_topic.topic_dir, status=RawStatus.PENDING)
    assert len(pending_after) == 2


def test_sleep_marks_consolidated_only_after_state_md_written(
    seeded_topic: SeededTopic,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If write_state somehow ran but mark_status didn't, raws would be lost.

    We verify the order via spy: write_state must complete before any
    mark_status call observes the new status.
    """
    from remory.sleep import orchestrator as orch

    write_state_calls: list[float] = []
    mark_status_calls: list[float] = []

    real_ws = orch.write_state
    real_ms = orch.mark_status

    import time

    def spy_ws(path: Path, doc: object) -> None:
        write_state_calls.append(time.monotonic())
        real_ws(path, doc)  # type: ignore[arg-type]

    def spy_ms(entries: object, status: object) -> object:
        mark_status_calls.append(time.monotonic())
        return real_ms(entries, status)  # type: ignore[arg-type]

    monkeypatch.setattr(orch, "write_state", spy_ws)
    monkeypatch.setattr(orch, "mark_status", spy_ms)

    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))
    sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    assert write_state_calls and mark_status_calls
    assert write_state_calls[0] < mark_status_calls[0]


def test_sleep_meta_updates_last_consolidated_and_pending_count_zero(
    seeded_topic: SeededTopic,
) -> None:
    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))
    before = datetime.now(UTC)
    sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    after = datetime.now(UTC)
    meta = read_meta(seeded_topic.topic_dir)
    assert meta.pending_count == 0
    assert meta.last_consolidated is not None
    assert before <= meta.last_consolidated <= after


def test_sleep_meta_total_entries_unchanged_by_sleep(
    seeded_topic: SeededTopic,
) -> None:
    """D3: total_entries is the count of raw files; sleep does not change it."""
    meta_before = read_meta(seeded_topic.topic_dir)
    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))
    sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    meta_after = read_meta(seeded_topic.topic_dir)
    assert meta_after.total_entries == meta_before.total_entries


def test_sleep_state_frontmatter_entries_consolidated_increments_by_n(
    seeded_topic: SeededTopic,
) -> None:
    """D3: entries_consolidated lives in state.md frontmatter, NOT meta.yaml."""
    state = read_state(paths.state_file(seeded_topic.topic_dir))
    before = state.frontmatter.entries_consolidated
    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))
    sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    after = read_state(paths.state_file(seeded_topic.topic_dir)).frontmatter.entries_consolidated
    assert after == before + len(seeded_topic.pending_paths)


def test_sleep_lock_busy_raises_lock_busy_error_no_writes(
    seeded_topic: SeededTopic,
    multi_process_lock_holder: Callable[[Path], object],
) -> None:
    multi_process_lock_holder(seeded_topic.topic_dir)
    state_before = paths.state_file(seeded_topic.topic_dir).read_bytes()
    meta_before = read_meta(seeded_topic.topic_dir)
    backend = FakeBackend(headless_results=[])
    with pytest.raises(LockBusyError):
        sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    # No writes.
    assert paths.state_file(seeded_topic.topic_dir).read_bytes() == state_before
    assert read_meta(seeded_topic.topic_dir) == meta_before
    assert not paths.backups_dir(seeded_topic.topic_dir).exists() or not list(
        paths.backups_dir(seeded_topic.topic_dir).glob("*.bak")
    )


def test_sleep_dry_run_writes_nothing_holds_lock_skips_critique(
    seeded_topic: SeededTopic,
) -> None:
    """D8: dry_run holds lock, runs stages 1+2, skips critique, writes nothing."""
    state_before = paths.state_file(seeded_topic.topic_dir).read_bytes()
    meta_before = read_meta(seeded_topic.topic_dir)
    # Only extract + 2 merge sections (draft + revise each = 4) -- NO critique.
    extract = _result(_job_profile_extract_payload(seeded_topic))
    backend = FakeBackend(
        headless_results=[
            extract,
            _result("(skills) draft\n"),
            _result("(skills) revised\n"),
            _result("(constraints) draft\n"),
            _result("(constraints) revised\n"),
        ]
    )
    result = sleep(topic_dir=seeded_topic.topic_dir, backend=backend, dry_run=True)
    # Nothing on disk changed.
    assert paths.state_file(seeded_topic.topic_dir).read_bytes() == state_before
    assert read_meta(seeded_topic.topic_dir) == meta_before
    assert not paths.review_file(seeded_topic.topic_dir).exists()
    assert not paths.backups_dir(seeded_topic.topic_dir).exists() or not list(
        paths.backups_dir(seeded_topic.topic_dir).glob("*.bak")
    )
    # Backend was called for stages 1+2 only (no critique call).
    agents = [c["agent"] for c in backend.headless_calls]
    assert "critic" not in agents
    # Result carries the proposed text.
    assert any("DRY-RUN" in n for n in result.notes)
    assert any("proposed_state_md" in n for n in result.notes)


def test_sleep_section_outcomes_match_schema_order(
    seeded_topic: SeededTopic,
) -> None:
    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))
    result = sleep(topic_dir=seeded_topic.topic_dir, backend=backend)
    schema = load_builtin("job-profile")
    assert [o.section_id for o in result.section_outcomes] == [s.id for s in schema.sections]


def test_sleep_no_state_md_skips_backup_proceeds_with_empty_current_text(
    seeded_topic_factory: Callable[..., SeededTopic],
) -> None:
    """D7 first arm: no state.md -> skip backup, proceed."""
    seeded = seeded_topic_factory(seed_state=False, pending_count=1)
    state_path = paths.state_file(seeded.topic_dir)
    assert not state_path.exists()
    p0 = seeded.pending_paths[0]
    rel0 = f"raw/{p0.parent.name}/{p0.name}"
    payload = json.dumps({"skills_and_strengths": [{"text": "x", "evidence": rel0}]})
    backend = FakeBackend(
        headless_results=[
            _result(payload),
            _result("draft\n"),
            _result("revised\n"),
            _result("# Review\n\nok\n"),
        ]
    )
    result = sleep(topic_dir=seeded.topic_dir, backend=backend)
    assert result.backup_path is None
    # state.md created fresh.
    assert state_path.exists()


# ---------------------------------------------------------------------------
# Schema-drift detection (bidirectional walk)
# ---------------------------------------------------------------------------


def _seed_state_with_drift_section(
    seeded: SeededTopic,
    *,
    drift_title: str,
    drift_body: str,
) -> None:
    """Overwrite state.md with one that has all schema sections plus an extra.

    The schema-driven sections each get an empty-ish body; the orphan
    section is the one we expect the orchestrator to detect as drift.
    """
    schema = load_builtin(seeded.schema_name)
    sections: list[StateSection] = [StateSection(title=s.title, body="\n") for s in schema.sections]
    sections.append(StateSection(title=drift_title, body=drift_body))
    doc = StateDoc(
        frontmatter=StateFrontmatter(
            schema=seeded.schema_name,
            schema_version=schema.version,
            last_consolidated=None,
            entries_consolidated=0,
        ),
        sections=sections,
    )
    state_path = paths.state_file(seeded.topic_dir)
    with topic_lock(seeded.topic_dir):
        write_state(state_path, doc)


def test_sleep_drops_drift_section_with_warning_log_and_note_and_with_warnings_status(
    seeded_topic: SeededTopic,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drift section is dropped with a WARNING log + note + WITH_WARNINGS status.

    Pre-seed state.md with sections matching the job-profile schema plus
    one extra "X-orphan" section. After sleep, the rewritten state.md
    must NOT contain the orphan, but the user has a recovery path: the
    pre-sleep .bak captures the original. The drift is loud, not silent.
    """
    drift_title = "X-orphan"
    drift_body = "user-authored content the schema does not know about\n"
    _seed_state_with_drift_section(seeded_topic, drift_title=drift_title, drift_body=drift_body)
    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))

    with caplog.at_level(logging.WARNING, logger="remory.sleep.orchestrator"):
        result = sleep(topic_dir=seeded_topic.topic_dir, backend=backend)

    # Status flips to WITH_WARNINGS purely because of the drop.
    assert result.status is SleepStatus.SUCCESS_WITH_WARNINGS
    # Note is present (literal format pinned).
    expected_note = f"dropped drift section '{drift_title}' (not in schema; see logs)"
    assert expected_note in result.notes
    # Exactly one drift WARNING record, with the structured extras we promised.
    drift_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and getattr(r, "drift_section_title", None) == drift_title
    ]
    assert len(drift_records) == 1
    rec = drift_records[0]
    assert getattr(rec, "stage", None) == "merge"
    assert getattr(rec, "topic", None) == seeded_topic.topic_dir.name
    assert getattr(rec, "sleep_run_id", None) == result.run_id
    assert getattr(rec, "dropped_content_preview", None) == drift_body[:200]
    # Rewritten state.md no longer carries the orphan.
    state_after = read_state(paths.state_file(seeded_topic.topic_dir))
    assert drift_title not in {s.title for s in state_after.sections}
    # Backup exists at result.backup_path; it is the recovery path.
    assert result.backup_path is not None
    assert result.backup_path.exists()


def test_sleep_no_drift_does_not_emit_warning_or_note_keeps_success_status(
    seeded_topic: SeededTopic,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When state.md sections all match the schema, drift detection is silent.

    Default seeded state.md has exactly the schema's sections; there is
    no drift to surface. Status stays SUCCESS, no drift note appears,
    and no WARNING records about drift are emitted.
    """
    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))

    with caplog.at_level(logging.WARNING, logger="remory.sleep.orchestrator"):
        result = sleep(topic_dir=seeded_topic.topic_dir, backend=backend)

    assert result.status is SleepStatus.SUCCESS
    assert not any("dropped drift section" in n for n in result.notes)
    drift_records = [
        r for r in caplog.records if getattr(r, "drift_section_title", None) is not None
    ]
    assert drift_records == []


def test_sleep_drift_drop_preview_truncates_to_200_chars(
    seeded_topic: SeededTopic,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The WARNING record's content preview is exactly the first 200 chars.

    No truncation marker, no whitespace tweaks. The user reading the
    log can copy the preview verbatim and grep the .bak for context.
    """
    drift_title = "X-orphan"
    drift_body = ("a" * 500) + "\n"
    _seed_state_with_drift_section(seeded_topic, drift_title=drift_title, drift_body=drift_body)
    backend = FakeBackend(headless_results=_all_responses_for_full_pipeline(seeded_topic))

    with caplog.at_level(logging.WARNING, logger="remory.sleep.orchestrator"):
        sleep(topic_dir=seeded_topic.topic_dir, backend=backend)

    drift_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and getattr(r, "drift_section_title", None) == drift_title
    ]
    assert len(drift_records) == 1
    preview = getattr(drift_records[0], "dropped_content_preview", None)
    assert isinstance(preview, str)
    assert preview == "a" * 200


# ---------------------------------------------------------------------------
# SectionOutcome / SleepResult sanity
# ---------------------------------------------------------------------------


def test_section_outcome_dataclass_is_frozen() -> None:
    """Frozen dataclasses raise FrozenInstanceError on attribute assignment."""
    from dataclasses import FrozenInstanceError

    o = SectionOutcome(section_id="x", candidates_count=0, action="llm_merge")
    with pytest.raises(FrozenInstanceError):
        o.section_id = "y"  # type: ignore[misc] # frozen-dataclass write probe
