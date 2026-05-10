"""Unit tests for ``remory.sleep.critique``."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remory.backends.base import HeadlessMeta, HeadlessResult
from remory.schema import load_builtin
from remory.sleep.critique import CritiqueError, write_review
from remory.topic import Knobs, Topic, TopicMeta
from tests.fakes.fake_backend import FakeBackend


def _result(text: str) -> HeadlessResult:
    return HeadlessResult(
        text=text,
        session_id="s",
        duration_ms=1,
        num_turns=1,
        stop_reason="end_turn",
        meta=HeadlessMeta(raw_envelope=None),
    )


def _topic(tmp_path: Path) -> Topic:
    schema = load_builtin("job-profile")
    meta = TopicMeta(
        schema="job-profile",
        schema_version=1,
        created=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        knobs=Knobs(tone="warm", strictness="balanced"),
    )
    return Topic(name="job-profile", dir=tmp_path, meta=meta, schema=schema)


def test_write_review_atomic_replaces_existing_file(tmp_path: Path) -> None:
    review_path = tmp_path / "_review.md"
    review_path.write_text("OLD\n", encoding="utf-8")
    backend = FakeBackend(headless_results=[_result("# Review\n\nfresh content\n")])
    write_review(
        backend=backend,
        topic=_topic(tmp_path),
        state_md_text="(state text)",
        review_path=review_path,
    )
    assert review_path.read_text(encoding="utf-8") == "# Review\n\nfresh content\n"


def test_write_review_empty_backend_output_raises_critique_error(tmp_path: Path) -> None:
    review_path = tmp_path / "_review.md"
    backend = FakeBackend(headless_results=[_result("   \n\t\n")])
    with pytest.raises(CritiqueError, match="empty"):
        write_review(
            backend=backend,
            topic=_topic(tmp_path),
            state_md_text="(state text)",
            review_path=review_path,
        )
    # No file was written on failure.
    assert not review_path.exists()


def test_write_review_does_not_modify_state_md_mtime(tmp_path: Path) -> None:
    state_path = tmp_path / "state.md"
    state_path.write_text("---\nschema: job-profile\nschema_version: 1\n---\n\n# A\n\nb\n")
    review_path = tmp_path / "_review.md"
    before = state_path.stat().st_mtime_ns
    # Sleep briefly to ensure any spurious mtime change would be visible.
    time.sleep(0.01)
    backend = FakeBackend(headless_results=[_result("review body\n")])
    write_review(
        backend=backend,
        topic=_topic(tmp_path),
        state_md_text=state_path.read_text(encoding="utf-8"),
        review_path=review_path,
    )
    after = state_path.stat().st_mtime_ns
    assert after == before


def test_write_review_invokes_backend_with_critic_agent(tmp_path: Path) -> None:
    backend = FakeBackend(headless_results=[_result("body\n")])
    write_review(
        backend=backend,
        topic=_topic(tmp_path),
        state_md_text="x",
        review_path=tmp_path / "_review.md",
    )
    assert backend.headless_calls[0]["agent"] == "critic"
