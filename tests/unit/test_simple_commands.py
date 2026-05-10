"""Light-touch contract tests for state/recent/review/ingest/topics/stats/--version."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from remory.cli.errors import TopicMissingError
from remory.commands import (
    ingest_cmd,
    recent_cmd,
    review_cmd,
    state_cmd,
    stats_cmd,
    topics_cmd,
    version_cmd,
)


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def test_run_version_returns_remory_followed_by_pep440_version_string() -> None:
    out = version_cmd.run_version()
    # Format: ``remory <version>``; <version> is a non-empty token.
    assert out.startswith("remory ")
    version = out[len("remory ") :]
    assert version
    # PEP-440 looks like 0.1.0 etc; allow any non-whitespace.
    assert re.match(r"^[\w.+\-]+$", version)


# ---------------------------------------------------------------------------
# read-only commands without a topic
# ---------------------------------------------------------------------------


def test_run_state_raises_topic_missing_when_topic_absent(
    isolated_xdg: Path,
) -> None:
    del isolated_xdg
    with pytest.raises(TopicMissingError):
        state_cmd.run_state(topic_name="nope")


def test_run_recent_raises_topic_missing_when_topic_absent(
    isolated_xdg: Path,
) -> None:
    del isolated_xdg
    with pytest.raises(TopicMissingError):
        recent_cmd.run_recent(topic_name="nope", n=5)


def test_run_review_raises_topic_missing_when_topic_absent(
    isolated_xdg: Path,
) -> None:
    del isolated_xdg
    with pytest.raises(TopicMissingError):
        review_cmd.run_review(topic_name="nope")


def test_run_ingest_raises_topic_missing_when_topic_absent(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    del isolated_xdg
    f = tmp_path / "note.md"
    f.write_text("hello\n", encoding="utf-8")
    with pytest.raises(TopicMissingError):
        ingest_cmd.run_ingest(topic_name="nope", file=f)


def test_run_topics_emits_no_topics_message_when_dir_empty(
    isolated_xdg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del isolated_xdg
    topics_cmd.run_topics()
    out = capsys.readouterr().out
    assert "No topics yet" in out


def test_run_stats_emits_no_topics_message_when_dir_empty(
    isolated_xdg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del isolated_xdg
    stats_cmd.run_stats()
    out = capsys.readouterr().out
    assert "No topics yet" in out


def test_run_stats_table_includes_streak_column_per_topic(
    isolated_xdg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Header row carries the streak column; per-topic rows render `<N> days`."""
    del isolated_xdg
    from remory.commands.init_cmd import run_init

    run_init(topic_name="workout", schema_name="workout")
    stats_cmd.run_stats()
    out = capsys.readouterr().out
    # Header row carries streak
    assert "topic" in out
    assert "entries" in out
    assert "pending" in out
    assert "last sleep" in out
    assert "streak" in out
    # Empty topic just initialised -> 0 days streak
    assert "0 days" in out
    # Footer carries pluralised topic count and total entries
    assert "1 topic" in out
    assert "0 entries total" in out


# ---------------------------------------------------------------------------
# Read-only commands against a populated topic
# ---------------------------------------------------------------------------


def test_run_state_prints_state_md_contents(
    isolated_xdg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from remory.commands.init_cmd import run_init

    run_init(topic_name="workout", schema_name="workout")
    state_cmd.run_state(topic_name="workout")
    out = capsys.readouterr().out
    assert "schema: workout" in out


def test_run_review_emits_no_review_yet_message_when_review_md_absent(
    isolated_xdg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from remory.commands.init_cmd import run_init

    run_init(topic_name="workout", schema_name="workout")
    review_cmd.run_review(topic_name="workout")
    out = capsys.readouterr().out
    assert "No review yet" in out


def test_run_ingest_writes_raw_entry_with_ingested_source(
    isolated_xdg: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from remory.commands.init_cmd import run_init
    from remory.raw import RawSource, list_raw

    run_init(topic_name="workout", schema_name="workout")
    f = tmp_path / "note.md"
    f.write_text("session log\n", encoding="utf-8")
    ingest_cmd.run_ingest(topic_name="workout", file=f)
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    entries = list_raw(topic_dir, status=None)
    assert len(entries) == 1
    assert entries[0].frontmatter.source is RawSource.INGESTED
    assert "session log" in entries[0].body
    out = capsys.readouterr().out
    assert "Ingested" in out
