"""Wizard COMMIT block tests (Phase 5, consolidated plan §11.6).

Pins: data_dir + topics_dir creation, per-topic write order, lock
release, partial-failure (with prior, without prior), refusal at
COMMIT for existing topics, about-me.md ordering + failure surfacing.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from remory import paths
from remory.cli.errors import TopicExistsError
from remory.locking import is_locked
from remory.state import read_state
from remory.topic import read_meta
from remory.wizard import (
    WizardAboutMeError,
    WizardAnswers,
    WizardCommitPartialError,
    WizardKnobs,
    commit,
)
from remory.wizard import _commit as _commit_mod

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only locking under test",
)

LETTER = "Hi Sam. You picked workout. I'll keep what you bring me here."


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def _make_answers(topics: list[str], **knobs: WizardKnobs) -> WizardAnswers:
    """Convenience: build a WizardAnswers with sensible knob defaults."""
    knobs_by_topic: dict[str, WizardKnobs] = {}
    for t in topics:
        knobs_by_topic[t] = knobs.get(t, WizardKnobs(tone="warm", strictness="balanced"))
    return WizardAnswers(
        version=1,
        name="Sam",
        chosen_topics=tuple(topics),
        knobs_by_topic=knobs_by_topic,
        wish="stop forgetting",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_commit_creates_data_dir_and_topics_dir_when_absent(
    isolated_xdg: Path,
) -> None:
    data_dir = isolated_xdg / "data"
    assert not data_dir.exists()
    answers = _make_answers(["workout"])
    commit(answers, LETTER, data_dir=data_dir)
    assert data_dir.is_dir()
    assert (data_dir / "topics").is_dir()


def test_commit_writes_meta_state_claude_md_and_about_me_for_one_topic_happy_path(
    isolated_xdg: Path,
) -> None:
    data_dir = isolated_xdg / "data"
    answers = _make_answers(["workout"])
    commit(answers, LETTER, data_dir=data_dir)

    topic_dir = data_dir / "topics" / "workout"
    assert topic_dir.is_dir()
    meta = read_meta(topic_dir)
    assert meta.schema_name == "workout"
    assert meta.knobs.tone in {"warm", "balanced", "direct"}

    state_path = paths.state_file(topic_dir)
    doc = read_state(state_path)
    assert len(doc.sections) > 0

    claude_md = paths.claude_md_file(topic_dir).read_text(encoding="utf-8")
    assert "Topic: workout" in claude_md

    about_me = paths.about_me_file(data_dir).read_text(encoding="utf-8")
    assert about_me.startswith(LETTER)
    assert "name: Sam" in about_me


def test_commit_writes_artefacts_in_selection_order_for_two_topics(
    isolated_xdg: Path,
) -> None:
    """Selection order matters: topic dirs created in the order given."""
    data_dir = isolated_xdg / "data"
    answers = _make_answers(["workout", "coaching"])
    commit(answers, LETTER, data_dir=data_dir)

    topics_root = data_dir / "topics"
    # Both topic dirs exist.
    assert (topics_root / "workout").is_dir()
    assert (topics_root / "coaching").is_dir()
    # Modification times preserve selection order — workout first.
    assert (topics_root / "workout").stat().st_mtime <= (topics_root / "coaching").stat().st_mtime


def test_commit_writes_artefacts_in_selection_order_for_three_topics(
    isolated_xdg: Path,
) -> None:
    data_dir = isolated_xdg / "data"
    answers = _make_answers(["coaching", "workout", "job-profile"])
    commit(answers, LETTER, data_dir=data_dir)

    topics_root = data_dir / "topics"
    for name in ("coaching", "workout", "job-profile"):
        assert (topics_root / name).is_dir(), f"missing {name}"
    # about-me.md topics line preserves selection order.
    about_me = paths.about_me_file(data_dir).read_text(encoding="utf-8")
    assert "topics: coaching, workout, job-profile" in about_me


def test_commit_releases_topic_locks_after_per_topic_writes_complete(
    isolated_xdg: Path,
) -> None:
    """After commit returns, no topic lock should still be held."""
    data_dir = isolated_xdg / "data"
    answers = _make_answers(["workout", "coaching"])
    commit(answers, LETTER, data_dir=data_dir)
    for name in ("workout", "coaching"):
        topic_dir = data_dir / "topics" / name
        assert is_locked(topic_dir) is False


def test_commit_writes_about_me_after_all_topics_complete(
    isolated_xdg: Path,
) -> None:
    """about-me.md is written *after* every topic dir is created."""
    data_dir = isolated_xdg / "data"
    answers = _make_answers(["workout", "coaching"])
    commit(answers, LETTER, data_dir=data_dir)
    about_me = paths.about_me_file(data_dir)
    assert about_me.exists()
    # Both topic dirs exist already by the time about-me.md is on disk.
    assert (data_dir / "topics" / "workout").is_dir()
    assert (data_dir / "topics" / "coaching").is_dir()


# ---------------------------------------------------------------------------
# Refusal at COMMIT (existing topic) — §2 #2
# ---------------------------------------------------------------------------


def test_commit_refuses_at_commit_when_topic_dir_already_exists_with_topic_exists_error(
    isolated_xdg: Path,
) -> None:
    """Filtering at the menu is rejected per §2 #2; refusal fires at COMMIT."""
    data_dir = isolated_xdg / "data"
    pre_existing = data_dir / "topics" / "workout"
    pre_existing.mkdir(parents=True, exist_ok=True)

    answers = _make_answers(["workout"])
    with pytest.raises(TopicExistsError) as ei:
        commit(answers, LETTER, data_dir=data_dir)
    assert ei.value.name == "workout"


# ---------------------------------------------------------------------------
# Partial failures
# ---------------------------------------------------------------------------


def test_commit_raises_partial_when_second_topic_write_state_fails_leaving_first_topic_intact(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the second topic's state write to fail; first topic stays."""
    data_dir = isolated_xdg / "data"
    answers = _make_answers(["workout", "coaching"])

    # Patch write_state to fail on the second invocation only.
    real_write_state = _commit_mod.write_state
    call_count = {"n": 0}

    def fail_on_second(state_path: Path, doc: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated disk-full on second topic")
        real_write_state(state_path, doc)  # type: ignore[arg-type]

    monkeypatch.setattr(_commit_mod, "write_state", fail_on_second)

    with pytest.raises(WizardCommitPartialError) as ei:
        commit(answers, LETTER, data_dir=data_dir)
    assert ei.value.failed_topic == "coaching"
    assert ei.value.prior_topic == "workout"

    # First topic survived intact.
    assert (data_dir / "topics" / "workout" / "meta.yaml").exists()
    assert (data_dir / "topics" / "workout" / "state.md").exists()


def test_commit_raises_partial_with_no_prior_clause_when_first_topic_fails(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-topic failure → prior_topic is None."""
    data_dir = isolated_xdg / "data"
    answers = _make_answers(["workout", "coaching"])

    def always_fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("simulated immediate disk-full")

    monkeypatch.setattr(_commit_mod, "write_meta", always_fail)

    with pytest.raises(WizardCommitPartialError) as ei:
        commit(answers, LETTER, data_dir=data_dir)
    assert ei.value.failed_topic == "workout"
    assert ei.value.prior_topic is None


# ---------------------------------------------------------------------------
# about-me.md failure (after all topics complete)
# ---------------------------------------------------------------------------


def test_commit_raises_about_me_error_when_about_me_write_fails_after_topics_complete(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = isolated_xdg / "data"
    answers = _make_answers(["workout"])

    real_atomic_write_text = _commit_mod.atomic_write_text

    def fail_on_about_me(path: Path, content: str, **kwargs: object) -> None:
        if path.name == "about-me.md":
            raise OSError("simulated disk-full on about-me.md")
        real_atomic_write_text(path, content, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_commit_mod, "atomic_write_text", fail_on_about_me)

    with pytest.raises(WizardAboutMeError):
        commit(answers, LETTER, data_dir=data_dir)

    # The topic dir + meta + state survived.
    assert (data_dir / "topics" / "workout" / "meta.yaml").exists()
    assert (data_dir / "topics" / "workout" / "state.md").exists()
