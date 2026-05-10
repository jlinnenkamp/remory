"""Tests for :func:`remory.state.is_canonical`.

The Phase 4 doctor's ``--strict`` check uses this helper to detect
hand-edited ``state.md`` files whose YAML frontmatter would be
re-formatted by the next sleep.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from remory.state import (
    StateDoc,
    StateFrontmatter,
    StateParseError,
    StateSection,
    is_canonical,
    render_state,
)


def _seed_canonical(tmp_path: Path) -> Path:
    """Write a canonical state.md and return its path."""
    doc = StateDoc(
        frontmatter=StateFrontmatter(
            schema="job-profile",
            schema_version=1,
            last_consolidated=datetime(2026, 5, 9, 9, 0, tzinfo=UTC),
            entries_consolidated=1,
        ),
        sections=[
            StateSection(title="Skills and strengths", body="content\n"),
        ],
    )
    text = render_state(doc)
    path = tmp_path / "state.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_is_canonical_returns_true_for_render_state_output_unmodified(tmp_path: Path) -> None:
    path = _seed_canonical(tmp_path)
    assert is_canonical(path) is True


def test_is_canonical_returns_false_for_handedited_unsorted_keys(tmp_path: Path) -> None:
    text = (
        "---\n"
        # Wrong key order: schema_version first, then schema.
        "schema_version: 1\n"
        "schema: job-profile\n"
        "last_consolidated: 2026-05-09T09:00:00Z\n"
        "entries_consolidated: 0\n"
        "---\n\n"
        "# Skills and strengths\n\n"
    )
    path = tmp_path / "state.md"
    path.write_text(text, encoding="utf-8")
    assert is_canonical(path) is False


def test_is_canonical_returns_false_for_single_quoted_iso_datetimes(tmp_path: Path) -> None:
    text = (
        "---\n"
        "schema: job-profile\n"
        "schema_version: 1\n"
        # Single-quoted datetime; canonical form is unquoted.
        "last_consolidated: '2026-05-09T09:00:00Z'\n"
        "entries_consolidated: 0\n"
        "---\n\n"
        "# Skills and strengths\n\n"
    )
    path = tmp_path / "state.md"
    path.write_text(text, encoding="utf-8")
    assert is_canonical(path) is False


def test_is_canonical_does_not_modify_file_on_disk(tmp_path: Path) -> None:
    path = _seed_canonical(tmp_path)
    original = path.read_bytes()
    is_canonical(path)
    assert path.read_bytes() == original


def test_is_canonical_propagates_state_parse_error_to_caller(tmp_path: Path) -> None:
    path = tmp_path / "state.md"
    path.write_text("not a state.md\n", encoding="utf-8")
    with pytest.raises(StateParseError):
        is_canonical(path)
