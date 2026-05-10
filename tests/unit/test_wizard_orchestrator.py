"""Wizard orchestrator wiring tests (Phase 5, consolidated plan §11.7).

Pins: linear step ordering, per-topic block runs only for selected
topics, schema-defaults fall-through when option questions skipped,
pre-COMMIT KeyboardInterrupt leaves no files behind.
"""

from __future__ import annotations

import io
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.console import Console

from remory import paths
from remory.topic import read_meta
from remory.wizard._orchestrator import run_wizard
from tests.fakes.fake_backend import FakeBackend
from tests.fakes.scripted_input import ScriptedInput

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only locking under test",
)


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def _quiet_console() -> Console:
    return Console(file=io.StringIO(), color_system=None, no_color=True)


def test_run_wizard_threads_answers_through_all_steps_in_linear_order(
    isolated_xdg: Path,
) -> None:
    """All steps run; outputs land in expected places."""
    data_dir = isolated_xdg / "data"
    # Script: name=Sam, picks=1,2 (coaching, job-profile in lex order),
    # coaching q1=1 (warm), q2=1 (gentle),
    # job-profile q1=1 (warm), q2=1 (gentle),
    # wish=stop forgetting.
    fake = ScriptedInput(["Sam", "1,2", "1", "1", "1", "1", "stop forgetting"])
    backend = FakeBackend.with_letter_text("Hi Sam, I heard you. I'll keep what you bring.")

    run_wizard(
        backend_factory=lambda: backend,
        console=_quiet_console(),
        input_fn=fake,
        data_dir=data_dir,
    )

    # Both selected topics are on disk; the third (workout) is not.
    assert (data_dir / "topics" / "coaching").is_dir()
    assert (data_dir / "topics" / "job-profile").is_dir()
    assert not (data_dir / "topics" / "workout").exists()
    # about-me.md exists with the model's letter.
    about_me = paths.about_me_file(data_dir).read_text(encoding="utf-8")
    assert about_me.startswith("Hi Sam, I heard you.")
    assert "name: Sam" in about_me
    assert "wish: stop forgetting" in about_me


def test_run_wizard_skips_per_topic_block_for_unselected_topics(
    isolated_xdg: Path,
) -> None:
    """User picks topic 2 only — only one Q1+Q2 pair is asked."""
    data_dir = isolated_xdg / "data"
    # Sam, "2" (job-profile only), q1=1 (warm), q2=2 (rigorous), wish.
    fake = ScriptedInput(["Sam", "2", "1", "2", "stop forgetting"])
    backend = FakeBackend.with_letter_text("paragraph")

    run_wizard(
        backend_factory=lambda: backend,
        console=_quiet_console(),
        input_fn=fake,
        data_dir=data_dir,
    )

    # Only one topic dir created.
    topics_root = data_dir / "topics"
    assert (topics_root / "job-profile").is_dir()
    assert not (topics_root / "workout").exists()
    assert not (topics_root / "coaching").exists()
    meta = read_meta(topics_root / "job-profile")
    assert meta.knobs.tone == "warm"
    assert meta.knobs.strictness == "rigorous"


def test_run_wizard_uses_schema_defaults_when_user_skips_q1_q2(
    isolated_xdg: Path,
) -> None:
    """Skip both option questions for workout → schema defaults applied."""
    data_dir = isolated_xdg / "data"
    # Sam, "2" (job-profile lex idx 2 = job-profile), q1=s, q2=s, wish.
    # job-profile defaults: tone=warm, strictness=balanced.
    fake = ScriptedInput(["Sam", "2", "s", "s", "stop forgetting"])
    backend = FakeBackend.with_letter_text("paragraph")

    run_wizard(
        backend_factory=lambda: backend,
        console=_quiet_console(),
        input_fn=fake,
        data_dir=data_dir,
    )

    meta = read_meta(data_dir / "topics" / "job-profile")
    assert meta.knobs.tone == "warm"  # job-profile default
    assert meta.knobs.strictness == "balanced"  # job-profile default


def test_run_wizard_pre_commit_keyboard_interrupt_propagates_without_writing_files(
    isolated_xdg: Path,
) -> None:
    """Ctrl+C before COMMIT → no topic dirs created, no about-me.md."""
    data_dir = isolated_xdg / "data"
    # Trigger KeyboardInterrupt right after collecting the name (idx 1
    # — the second prompt is the topic-pick).
    fake = ScriptedInput(["Sam"], raise_at=1)
    backend = FakeBackend.with_letter_text("never reached")

    with pytest.raises(KeyboardInterrupt):
        run_wizard(
            backend_factory=lambda: backend,
            console=_quiet_console(),
            input_fn=fake,
            data_dir=data_dir,
        )

    # Pre-COMMIT: no topic dirs, no about-me.md.
    assert not (data_dir / "topics").exists() or not any((data_dir / "topics").iterdir())
    assert not paths.about_me_file(data_dir).exists()
