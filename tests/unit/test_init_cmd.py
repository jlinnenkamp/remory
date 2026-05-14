"""Tests for ``remory init`` Phase 4 stub (consolidated plan §3.9)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from remory import paths
from remory.cli.errors import TopicExistsError
from remory.commands.init_cmd import run_init
from remory.locking import is_locked
from remory.schema import SchemaError
from remory.state import read_state
from remory.topic import read_meta
from remory.wizard import WizardRedirectError


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


# ---------------------------------------------------------------------------
# R2 — schema flag required
# ---------------------------------------------------------------------------


def test_run_init_without_schema_raises_wizard_redirect_with_new_r3_wording(
    isolated_xdg: Path,
) -> None:
    """R3 refresh: the Phase 4 'isn't built yet' wording is replaced; the
    new message redirects users to either ``--schema`` or the no-args
    wizard. ``WizardNotBuiltError`` remains as a one-release alias.
    """
    del isolated_xdg
    with pytest.raises(WizardRedirectError) as ei:
        run_init(topic_name="workout", schema_name=None)
    msg = str(ei.value)
    assert "Pass --schema to pick a built-in directly" in msg
    assert "--schema job-profile" in msg
    assert "--schema workout" in msg
    assert "--schema coaching" in msg
    assert "remory init`" in msg or "remory init " in msg
    # Phase 4 phrasing is gone.
    assert "isn't built yet" not in msg


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_run_init_unknown_schema_includes_did_you_mean_close_match(
    isolated_xdg: Path,
) -> None:
    del isolated_xdg
    with pytest.raises(SchemaError) as ei:
        run_init(topic_name="career", schema_name="jobprofile")
    msg = str(ei.value)
    assert "Did you mean: job-profile?" in msg
    assert "Available built-in schemas: coaching, job-profile, workout." in msg


def test_run_init_unknown_schema_omits_did_you_mean_when_no_close_match(
    isolated_xdg: Path,
) -> None:
    del isolated_xdg
    with pytest.raises(SchemaError) as ei:
        run_init(topic_name="career", schema_name="zzzzz")
    msg = str(ei.value)
    assert "Did you mean" not in msg


# ---------------------------------------------------------------------------
# Topic name validation
# ---------------------------------------------------------------------------


def test_run_init_rejects_invalid_topic_name_with_value_error(
    isolated_xdg: Path,
) -> None:
    del isolated_xdg
    with pytest.raises(ValueError, match="topic name"):
        run_init(topic_name="Bad Name!", schema_name="workout")


# ---------------------------------------------------------------------------
# D7 — existing topic refusal
# ---------------------------------------------------------------------------


def test_run_init_refuses_existing_topic_with_topic_exists_error(
    isolated_xdg: Path,
) -> None:
    run_init(topic_name="workout", schema_name="workout")
    with pytest.raises(TopicExistsError) as ei:
        run_init(topic_name="workout", schema_name="workout")
    assert ei.value.name == "workout"
    assert ei.value.topic_dir.exists()


# ---------------------------------------------------------------------------
# Successful creation: meta.yaml + state.md skeleton + CLAUDE.md
# ---------------------------------------------------------------------------


def test_run_init_creates_meta_yaml_state_md_skeleton_and_claude_md(
    isolated_xdg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_init(topic_name="workout", schema_name="workout")
    data_dir = isolated_xdg / "data"
    topic_dir = data_dir / "topics" / "workout"

    assert topic_dir.is_dir()
    meta = read_meta(topic_dir)
    assert meta.schema_name == "workout"
    assert meta.pending_count == 0
    assert meta.knobs.tone in {"warm", "balanced", "direct"}

    state_path = paths.state_file(topic_dir)
    doc = read_state(state_path)
    titles = [s.title for s in doc.sections]
    assert len(titles) > 0  # schema-defined sections were skeletoned

    claude_md = paths.claude_md_file(topic_dir).read_text(encoding="utf-8")
    assert "Topic: workout" in claude_md
    # Phase 6: the per-topic template uses backticks around state.md.
    assert "Do not edit `state.md`" in claude_md
    # Phase 6: the template carries the template-version stamp.
    assert "<!-- remory: template_version=1 -->" in claude_md

    out = capsys.readouterr().out
    assert "Topic 'workout' created" in out
    assert "remory chat workout" in out


def test_run_init_releases_topic_lock_after_writes_complete(
    isolated_xdg: Path,
) -> None:
    run_init(topic_name="coaching", schema_name="coaching")
    topic_dir = isolated_xdg / "data" / "topics" / "coaching"
    # After init returns, the lock file may exist on disk but must not
    # be held — a fresh acquirer should succeed.
    assert is_locked(topic_dir) is False
