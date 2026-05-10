"""Integration: init creates the topic; chat writes the first raw entry;
existing-topic refusal does not clobber state.md.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from remory import paths
from remory.cli.errors import TopicExistsError
from remory.commands.chat_cmd import run_chat
from remory.commands.init_cmd import run_init
from remory.raw import RawStatus, list_raw

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fake binary")


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def test_init_then_chat_writes_first_raw_entry(
    isolated_xdg: Path,
    fake_claude_on_path: tuple[Path],
) -> None:
    del fake_claude_on_path
    run_init(topic_name="workout", schema_name="workout")
    run_chat(topic_name="workout", continue_session=False)
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    pending = list_raw(topic_dir, status=RawStatus.PENDING)
    assert len(pending) == 1


def test_init_existing_topic_refusal_does_not_clobber_state_md(
    isolated_xdg: Path,
) -> None:
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    state_path = paths.state_file(topic_dir)
    original_bytes = state_path.read_bytes()
    with pytest.raises(TopicExistsError):
        run_init(topic_name="workout", schema_name="workout")
    assert state_path.read_bytes() == original_bytes
