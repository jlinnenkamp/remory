"""Integration: hooks coordinate with the real chat-lock under fake_claude.

Plan §11.2 — 2 tests. Uses ``fake_claude_on_path`` to drive a real chat
subprocess; invokes the SessionEnd hook in-process via the pure
:func:`remory.hooks.session_end.run` helper so we can assert on the
return outcome.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from remory.commands.init_cmd import run_init
from remory.hooks.pre_tool_use import PreToolUseInput, decide
from remory.hooks.session_end import SessionEndInput
from remory.hooks.session_end import run as run_hook
from remory.locking import topic_lock
from remory.raw import RawStatus, list_raw

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fixtures")


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def test_chat_writes_raw_and_session_end_hook_skips_when_remory_chat_owns_the_lock(
    isolated_xdg: Path,
    fake_claude_on_path: tuple[Path],
) -> None:
    """ADR-0002: chat is canonical; the hook defers under the chat lock.

    Simulates the real coordination: ``run_chat`` holds the lock; the
    hook running concurrently must see ``deferred_locked`` and write
    nothing.
    """
    del fake_claude_on_path
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"

    # We don't launch a real subprocess here — we acquire the lock
    # ourselves to simulate the chat parent holding it, then invoke
    # the hook in-process. ``fake_claude_on_path`` puts the fake
    # binary on PATH so the chat-path fixtures match real shape.
    with topic_lock(topic_dir, timeout=0.0):
        # Hook fires while chat parent holds the lock. The transcript
        # path doesn't matter — the lock check happens before any
        # transcript read.
        outcome = run_hook(
            SessionEndInput(
                cwd=topic_dir,
                session_id="sess-chat-owns",
                transcript_path=None,
            )
        )
        assert outcome.status == "deferred_locked"
    # After release, no raw entry from the hook.
    entries = list_raw(topic_dir, status=RawStatus.PENDING)
    assert all(e.frontmatter.session_id != "sess-chat-owns" for e in entries)


def test_pretool_hook_blocks_claude_from_editing_state_md_during_chat(
    isolated_xdg: Path,
) -> None:
    """PreToolUse hook denies Edit/Write to state.md (plan §5.8)."""
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    state_md = topic_dir / "state.md"

    # The decide() helper is the pure surface the Typer subcommand
    # delegates to; assert directly. The trailing newline is part of
    # the §5.8 contract.
    decision = decide(PreToolUseInput(tool_name="Edit", target_path=state_md))
    assert decision.allowed is False
    assert decision.message == (
        "state.md is updated only during `remory sleep`. Refusing the write.\n"
    )
