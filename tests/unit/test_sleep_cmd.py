"""Tests for ``remory sleep`` (single-topic + ``--if-due``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from remory.cli.errors import TopicMissingError
from remory.commands.sleep_cmd import run_sleep
from tests.fakes.fake_backend import FakeBackend


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def _seed_workout(isolated_xdg: Path, *, pending_count: int = 0) -> Path:
    from remory.commands.init_cmd import run_init
    from remory.locking import topic_lock
    from remory.topic import read_meta, write_meta

    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    if pending_count:
        meta = read_meta(topic_dir)
        with topic_lock(topic_dir, timeout=0.0):
            write_meta(
                topic_dir,
                meta.model_copy(update={"pending_count": pending_count}),
            )
    return topic_dir


# ---------------------------------------------------------------------------
# Single-topic mode
# ---------------------------------------------------------------------------


def test_run_sleep_missing_topic_without_if_due_raises_topic_missing_error(
    isolated_xdg: Path,
) -> None:
    del isolated_xdg
    with pytest.raises(TopicMissingError):
        run_sleep(
            topic_name="nope",
            if_due=False,
            dry_run=False,
            backend_factory=FakeBackend,
        )


def test_run_sleep_no_pending_short_circuits_to_no_pending_status(
    isolated_xdg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_workout(isolated_xdg, pending_count=0)
    backend = FakeBackend()
    run_sleep(
        topic_name="workout",
        if_due=False,
        dry_run=False,
        backend_factory=lambda: backend,
    )
    out = capsys.readouterr().out
    assert "Nothing pending" in out


# ---------------------------------------------------------------------------
# --if-due iteration (CC3)
# ---------------------------------------------------------------------------


def test_run_sleep_if_due_emits_friendly_message_when_no_topics(
    isolated_xdg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del isolated_xdg
    run_sleep(
        topic_name=None,
        if_due=True,
        dry_run=False,
        backend_factory=FakeBackend,
    )
    out = capsys.readouterr().out
    assert "No topics yet" in out


def test_run_sleep_if_due_skips_topics_below_threshold(
    isolated_xdg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_workout(isolated_xdg, pending_count=1)  # threshold is 3
    run_sleep(
        topic_name=None,
        if_due=True,
        dry_run=False,
        backend_factory=FakeBackend,
    )
    out = capsys.readouterr().out
    assert "No topics are at threshold" in out
