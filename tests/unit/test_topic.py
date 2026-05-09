"""Unit tests for ``remory.topic``."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from remory.locking import topic_lock
from remory.schema import BUILTIN_NAMES
from remory.topic import (
    Knobs,
    TopicMeta,
    TopicMetaError,
    load_topic,
    read_meta,
    write_meta,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock only")


def _make_topic(tmp_path: Path) -> Path:
    d = tmp_path / "job-profile"
    d.mkdir()
    return d


def _sample_meta() -> TopicMeta:
    return TopicMeta(
        schema="job-profile",
        schema_version=1,
        created=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        last_consolidated=datetime(2026, 5, 7, 18, 20, tzinfo=UTC),
        last_chat=datetime(2026, 5, 9, 9, 30, tzinfo=UTC),
        pending_count=2,
        total_entries=14,
        knobs=Knobs(tone="warm", strictness="balanced"),
    )


def test_round_trip_meta_yaml_via_write_meta_and_read_meta(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    meta = _sample_meta()
    with topic_lock(d):
        write_meta(d, meta)
    parsed = read_meta(d)
    assert parsed == meta


def test_write_meta_without_lock_raises_assertion_programming_bug(tmp_path: Path) -> None:
    """This is a programming-bug check --- catches the caller forgetting to acquire
    the topic lock at all. It does NOT test concurrent-release safety; a holder
    that releases between the assertion and the atomic rename is not caught.
    Phase 3 will revisit lock/write coupling when the sleep pipeline lands.
    """
    d = _make_topic(tmp_path)
    meta = _sample_meta()
    with pytest.raises(AssertionError, match="write_meta requires the topic lock"):
        write_meta(d, meta)


def test_meta_yaml_extra_top_level_key_raises_TopicMetaError(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    (d / "meta.yaml").write_text(
        "schema: job-profile\n"
        "schema_version: 1\n"
        "created: 2026-05-01T10:00:00Z\n"
        "pending_count: 0\n"
        "total_entries: 0\n"
        "knobs:\n"
        "  tone: warm\n"
        "  strictness: balanced\n"
        "rogue_key: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(TopicMetaError):
        read_meta(d)


def test_schema_alias_round_trip(tmp_path: Path) -> None:
    """YAML key ``schema:`` reads into ``schema_name`` and writes back as ``schema``."""
    d = _make_topic(tmp_path)
    meta = _sample_meta()
    with topic_lock(d):
        write_meta(d, meta)
    yaml_text = (d / "meta.yaml").read_text(encoding="utf-8")
    # On-disk key is ``schema``, not ``schema_name``.
    assert yaml_text.startswith("schema: job-profile\n")
    assert "schema_name:" not in yaml_text
    parsed = read_meta(d)
    assert parsed.schema_name == "job-profile"


def test_pending_count_negative_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        TopicMeta(
            schema="job-profile",
            schema_version=1,
            created=datetime(2026, 5, 1, tzinfo=UTC),
            pending_count=-1,
            total_entries=0,
            knobs=Knobs(tone="warm", strictness="balanced"),
        )


def test_load_topic_with_builtin_schema_name(tmp_path: Path) -> None:
    d = _make_topic(tmp_path)
    meta = _sample_meta()
    with topic_lock(d):
        write_meta(d, meta)
    topic = load_topic(d)
    assert topic.name == "job-profile"
    assert topic.dir == d
    assert topic.meta.schema_name == "job-profile"
    assert topic.schema.name == "job-profile"
    # Sanity: the schema we resolved came from the built-in set.
    assert "job-profile" in BUILTIN_NAMES
