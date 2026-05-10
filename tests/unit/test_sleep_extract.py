"""Unit tests for ``remory.sleep.extract``."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remory.backends.base import (
    BackendInvocationError,
    BackendOutputError,
    HeadlessMeta,
    HeadlessResult,
)
from remory.locking import topic_lock
from remory.raw import RawFrontmatter, RawSource, RawStatus, write_raw
from remory.schema import load_builtin
from remory.sleep.extract import (
    ExtractCandidate,
    ExtractError,
    extract,
)
from remory.topic import Knobs, Topic, TopicMeta
from tests.fakes.fake_backend import FakeBackend

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock only")


def _make_topic(tmp_path: Path) -> Topic:
    schema = load_builtin("job-profile")
    meta = TopicMeta(
        schema="job-profile",
        schema_version=1,
        created=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        knobs=Knobs(tone="warm", strictness="balanced"),
    )
    return Topic(name="job-profile", dir=tmp_path, meta=meta, schema=schema)


def _seed_pending_raw(topic_dir: Path, when: datetime) -> Path:
    fm = RawFrontmatter(
        created=when,
        source=RawSource.CHAT,
        status=RawStatus.PENDING,
        session_id="s",
    )
    with topic_lock(topic_dir):
        return write_raw(topic_dir, frontmatter=fm, body="body")


def _result(text: str) -> HeadlessResult:
    return HeadlessResult(
        text=text,
        session_id="s",
        duration_ms=1,
        num_turns=1,
        stop_reason="end_turn",
        meta=HeadlessMeta(raw_envelope=None),
    )


def _good_payload() -> str:
    return json.dumps(
        {
            "skills_and_strengths": [
                {
                    "text": "User prefers solo deep-focus work",
                    "evidence": "raw/2026/2026-05-09-0930.md",
                }
            ],
            "hard_constraints": [],
        }
    )


def test_extract_happy_path_validates_against_schema_section_ids(tmp_path: Path) -> None:
    topic = _make_topic(tmp_path)
    raw_path = _seed_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    assert raw_path.name == "2026-05-09-0930.md"
    from remory.raw import read_raw

    pending = [read_raw(raw_path)]
    backend = FakeBackend(headless_results=[_result(_good_payload())])
    result = extract(backend=backend, topic=topic, pending=pending)
    skills = result.for_section("skills_and_strengths")
    assert len(skills) == 1
    assert skills[0].text == "User prefers solo deep-focus work"
    assert skills[0].evidence == "raw/2026/2026-05-09-0930.md"
    # Empty section is preserved as empty tuple.
    assert result.for_section("hard_constraints") == ()
    # for_section on a section the LLM omitted entirely returns empty.
    assert result.for_section("evidence_log") == ()


def test_extract_unknown_section_id_raises_extract_error(tmp_path: Path) -> None:
    topic = _make_topic(tmp_path)
    raw_path = _seed_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    from remory.raw import read_raw

    pending = [read_raw(raw_path)]
    bad_payload = json.dumps(
        {"not_a_real_section": [{"text": "x", "evidence": "raw/2026/2026-05-09-0930.md"}]}
    )
    backend = FakeBackend(headless_results=[_result(bad_payload)])
    with pytest.raises(ExtractError, match="unknown section id"):
        extract(backend=backend, topic=topic, pending=pending)


def test_extract_invalid_json_retries_once_with_stricter_then_succeeds(
    tmp_path: Path,
) -> None:
    topic = _make_topic(tmp_path)
    raw_path = _seed_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    from remory.raw import read_raw

    pending = [read_raw(raw_path)]
    backend = FakeBackend(
        headless_results=[
            _result("not json at all"),
            _result(_good_payload()),
        ]
    )
    result = extract(backend=backend, topic=topic, pending=pending)
    assert len(result.for_section("skills_and_strengths")) == 1
    # Second call's prompt must have used the stricter clamp.
    second_prompt = backend.headless_calls[1]["prompt"]
    assert isinstance(second_prompt, str)
    assert "ONLY a JSON object" in second_prompt
    first_prompt = backend.headless_calls[0]["prompt"]
    assert isinstance(first_prompt, str)
    assert "ONLY a JSON object" not in first_prompt


def test_extract_invalid_json_twice_raises_extract_error(tmp_path: Path) -> None:
    topic = _make_topic(tmp_path)
    raw_path = _seed_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    from remory.raw import read_raw

    pending = [read_raw(raw_path)]
    backend = FakeBackend(
        headless_results=[
            _result("garbage 1"),
            _result("garbage 2"),
        ]
    )
    with pytest.raises(ExtractError, match="malformed"):
        extract(backend=backend, topic=topic, pending=pending)
    assert len(backend.headless_calls) == 2


def test_extract_passes_only_pending_entries_to_backend(tmp_path: Path) -> None:
    """Precondition: ``extract`` is the seam; orchestrator pre-filters PENDING.

    A non-PENDING entry in the input list raises before we touch the backend.
    """
    topic = _make_topic(tmp_path)
    consolidated_path = _seed_pending_raw(tmp_path, datetime(2026, 5, 8, 9, 30, tzinfo=UTC))
    from remory.raw import mark_status, read_raw

    with topic_lock(tmp_path):
        mark_status([read_raw(consolidated_path)], RawStatus.CONSOLIDATED)
    pending_path = _seed_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))

    consolidated_entry = read_raw(consolidated_path)
    pending_entry = read_raw(pending_path)
    assert consolidated_entry.frontmatter.status is RawStatus.CONSOLIDATED
    assert pending_entry.frontmatter.status is RawStatus.PENDING

    backend = FakeBackend(headless_results=[_result(_good_payload())])
    with pytest.raises(ExtractError, match="not PENDING"):
        extract(backend=backend, topic=topic, pending=[pending_entry, consolidated_entry])
    # Backend must NOT have been invoked once we discovered the bad input.
    assert backend.headless_calls == []


def test_extract_empty_pending_precondition_violated_raises(tmp_path: Path) -> None:
    topic = _make_topic(tmp_path)
    backend = FakeBackend(headless_results=[])
    with pytest.raises(ExtractError, match="non-empty"):
        extract(backend=backend, topic=topic, pending=[])
    assert backend.headless_calls == []


def test_extract_invocation_error_retries_three_times_then_raises(tmp_path: Path) -> None:
    """tenacity wraps invocation failures at 3 attempts; the wrapped error propagates."""
    topic = _make_topic(tmp_path)
    raw_path = _seed_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    from remory.raw import read_raw

    pending = [read_raw(raw_path)]
    backend = FakeBackend(
        headless_results=[
            BackendInvocationError("boom 1", exit_code=1),
            BackendInvocationError("boom 2", exit_code=1),
            BackendInvocationError("boom 3", exit_code=1),
        ]
    )
    with pytest.raises(BackendInvocationError):
        extract(backend=backend, topic=topic, pending=pending)
    assert len(backend.headless_calls) == 3


def test_extract_invocation_error_then_succeeds_within_three_attempts(tmp_path: Path) -> None:
    """Two invocation failures then a success: tenacity actually retries.

    Proves the policy retries (rather than stopping on first failure).
    """
    topic = _make_topic(tmp_path)
    raw_path = _seed_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    from remory.raw import read_raw

    pending = [read_raw(raw_path)]
    backend = FakeBackend(
        headless_results=[
            BackendInvocationError("boom 1", exit_code=1),
            BackendInvocationError("boom 2", exit_code=1),
            _result(_good_payload()),
        ]
    )
    result = extract(backend=backend, topic=topic, pending=pending)
    assert len(backend.headless_calls) == 3
    assert "skills_and_strengths" in result.candidates_by_section


def test_extract_candidate_evidence_regex_rejects_invalid_path() -> None:
    """D9 wire-format guard at model-validate time."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExtractCandidate(text="x", evidence="not/a/raw/path.txt")


def test_extract_candidate_evidence_regex_accepts_valid_path() -> None:
    candidate = ExtractCandidate(text="x", evidence="raw/2026/2026-05-09-0930.md")
    assert candidate.evidence == "raw/2026/2026-05-09-0930.md"


def test_extract_invocation_then_output_error_raises_extract_error(tmp_path: Path) -> None:
    """If invocation retries succeed but output still bad after stricter, ExtractError."""
    topic = _make_topic(tmp_path)
    raw_path = _seed_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    from remory.raw import read_raw

    pending = [read_raw(raw_path)]
    # The hand-handler runs after invocation-retries succeed (returns malformed
    # text). One retry with stricter -> still malformed -> ExtractError.
    backend = FakeBackend(
        headless_results=[
            _result("malformed 1"),
            _result("malformed 2"),
        ]
    )
    with pytest.raises(ExtractError):
        extract(backend=backend, topic=topic, pending=pending)


def test_extract_output_error_in_orchestrator_does_not_retry_via_tenacity(
    tmp_path: Path,
) -> None:
    """``BackendOutputError`` is not in the tenacity retry filter; only stricter handles it."""
    topic = _make_topic(tmp_path)
    raw_path = _seed_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    from remory.raw import read_raw

    pending = [read_raw(raw_path)]
    # Three malformed responses -> first triggers stricter; second is the
    # stricter call's response; both fail. We must have exactly 2 calls
    # (NOT 3+ from tenacity retries).
    backend = FakeBackend(
        headless_results=[
            BackendOutputError("output bad 1"),  # tenacity-level: NOT retried
            _result(_good_payload()),  # would only fire if tenacity retried
        ]
    )
    # Because BackendOutputError is raised by the backend (not by our parsing
    # post-invocation), the invocation-retries loop will see it and stop. The
    # hand-handler is for parsing failures, not invocation-time output errors.
    # We assert the call shape: BackendOutputError surfaces directly.
    with pytest.raises(BackendOutputError):
        extract(backend=backend, topic=topic, pending=pending)
    assert len(backend.headless_calls) == 1
