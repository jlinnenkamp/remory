"""§14-mandated end-to-end: init → chat → sleep against fake_claude.

This is the Phase 4 acceptance test. It covers the user-visible
sequence: ``remory init <topic> --schema <name>`` followed by ``remory
chat <topic>`` followed by ``remory sleep <topic>``, all against the
bundled fake binary on PATH.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from remory import paths
from remory.commands.chat_cmd import run_chat
from remory.commands.init_cmd import run_init
from remory.commands.sleep_cmd import run_sleep
from remory.raw import RawStatus, list_raw
from remory.topic import read_meta

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fake binary")


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def test_e2e_init_then_chat_then_sleep_against_fake_binary(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_on_path: tuple[Path],
) -> None:
    """Exercises the §14 chain: init → chat → sleep with fake_claude on PATH."""
    del fake_claude_on_path  # fixture sets PATH/FAKE_CLAUDE_HOME
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    assert topic_dir.is_dir()

    # Chat (fake_claude writes a canned transcript).
    run_chat(topic_name="workout", continue_session=False)
    entries = list_raw(topic_dir, status=RawStatus.PENDING)
    assert len(entries) == 1
    meta = read_meta(topic_dir)
    assert meta.pending_count == 1
    assert meta.last_chat is not None

    # Set up scripted fake_claude to drive sleep's headless calls.
    # workout schema has multiple non-append_only sections; only those
    # with candidates will trigger merge calls. Provide a permissive
    # extract response (one candidate per section so each merge fires).
    raw_path = entries[0].path
    rel = f"raw/{raw_path.parent.name}/{raw_path.name}"
    extract_payload = json.dumps(
        {
            "current_plan": [{"text": "3x/week strength", "evidence": rel}],
            "recent_sessions": [{"text": "lifted 100kg", "evidence": rel}],
            "progressions": [],
            "notes_and_injuries": [],
            "goals": [],
        }
    )
    # workout sleep policy is single_pass -> no critique step, no
    # draft+revise; one merge call per section that has candidates.
    # current_plan: 1 call. recent_sessions: 1 call. Total: extract + 2.
    responses = [
        extract_payload,
        "Updated plan: 3x/week strength.",  # current_plan merge
        "- lifted 100kg",  # recent_sessions merge
    ]
    script_path = isolated_xdg / "script.json"
    counter_path = isolated_xdg / "counter.txt"
    script_path.write_text(json.dumps(responses), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "scripted")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT_FILE", str(script_path))
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT_COUNTER_FILE", str(counter_path))

    run_sleep(topic_name="workout", if_due=False, dry_run=False)

    meta_after = read_meta(topic_dir)
    assert meta_after.pending_count == 0
    assert meta_after.last_consolidated is not None
    state_path = paths.state_file(topic_dir)
    assert state_path.exists()
    # Backup must have been taken.
    backups = list(paths.backups_dir(topic_dir).glob("state.md.*.bak"))
    assert len(backups) == 1
    # workout is single_pass -> no _review.md written.
    assert not paths.review_file(topic_dir).exists()
