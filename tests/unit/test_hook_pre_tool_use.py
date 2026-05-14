"""Unit tests for ``remory _hook pretool`` (plan §11.1 verbatim names).

Pin §5.8 refusal string and the symlink-resolution + basename-rejection
rules. The deny path must hit the verbatim message.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from remory.commands.init_cmd import run_init
from remory.hooks.pre_tool_use import (
    PRE_TOOL_USE_REFUSAL_MESSAGE,
    PreToolUseInput,
    decide,
)


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def _seed_workout(isolated_xdg: Path) -> Path:
    run_init(topic_name="workout", schema_name="workout")
    return isolated_xdg / "data" / "topics" / "workout"


def test_pre_tool_use_decide_allows_unrelated_tool_invocation(
    isolated_xdg: Path,
) -> None:
    """Tool name not in {Edit, Write} → allow."""
    topic_dir = _seed_workout(isolated_xdg)
    state_md = topic_dir / "state.md"
    decision = decide(PreToolUseInput(tool_name="Read", target_path=state_md))
    assert decision.allowed is True
    assert decision.message == ""


def test_pre_tool_use_decide_allows_edit_to_non_state_md_file_in_topic(
    isolated_xdg: Path,
) -> None:
    """Edit/Write to a non-state.md file inside a topic → allow."""
    topic_dir = _seed_workout(isolated_xdg)
    other = topic_dir / "notes.md"
    decision = decide(PreToolUseInput(tool_name="Edit", target_path=other))
    assert decision.allowed is True


def test_pre_tool_use_decide_allows_edit_to_state_md_outside_topics_tree(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    """A file named state.md outside <data_dir>/topics/<name>/ → allow.

    Pins the "basename-only matching is rejected" rule from plan §8.2.
    """
    del isolated_xdg
    outside = tmp_path / "elsewhere" / "state.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("not a remory state.md", encoding="utf-8")
    decision = decide(PreToolUseInput(tool_name="Edit", target_path=outside))
    assert decision.allowed is True


def test_pre_tool_use_decide_blocks_edit_to_topic_state_md(
    isolated_xdg: Path,
) -> None:
    """Edit on <data_dir>/topics/<name>/state.md → deny with §5.8 message."""
    topic_dir = _seed_workout(isolated_xdg)
    state_md = topic_dir / "state.md"
    decision = decide(PreToolUseInput(tool_name="Edit", target_path=state_md))
    assert decision.allowed is False
    assert decision.message == PRE_TOOL_USE_REFUSAL_MESSAGE


def test_pre_tool_use_decide_blocks_write_to_topic_state_md(
    isolated_xdg: Path,
) -> None:
    """Write on <data_dir>/topics/<name>/state.md → deny."""
    topic_dir = _seed_workout(isolated_xdg)
    state_md = topic_dir / "state.md"
    decision = decide(PreToolUseInput(tool_name="Write", target_path=state_md))
    assert decision.allowed is False
    assert decision.message == PRE_TOOL_USE_REFUSAL_MESSAGE


def test_pre_tool_use_decide_block_message_is_user_facing_string(
    isolated_xdg: Path,
) -> None:
    """Pin §5.8 — exact bytes including the trailing newline."""
    topic_dir = _seed_workout(isolated_xdg)
    state_md = topic_dir / "state.md"
    decision = decide(PreToolUseInput(tool_name="Edit", target_path=state_md))
    assert decision.allowed is False
    # Verbatim: trailing newline is part of the contract.
    assert decision.message == (
        "state.md is updated only during `remory sleep`. Refusing the write.\n"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks only")
def test_pre_tool_use_decide_resolves_symlinks_before_matching(
    isolated_xdg: Path, tmp_path: Path
) -> None:
    """A symlink targeting <topic>/state.md must be resolved → deny."""
    topic_dir = _seed_workout(isolated_xdg)
    real_state = topic_dir / "state.md"
    symlink = tmp_path / "alias-to-state.md"
    os.symlink(str(real_state), str(symlink))

    decision = decide(PreToolUseInput(tool_name="Edit", target_path=symlink))
    assert decision.allowed is False
    assert decision.message == PRE_TOOL_USE_REFUSAL_MESSAGE
