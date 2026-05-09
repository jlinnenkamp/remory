"""Unit tests for ``remory.state``."""

from __future__ import annotations

import inspect
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import remory.state as state_module
from remory.locking import topic_lock
from remory.schema import Schema, SchemaSection
from remory.state import (
    StateDoc,
    StateFrontmatter,
    StateParseError,
    StateSchemaMismatchError,
    StateSection,
    read_state,
    render_state,
    validate_state,
    write_state,
)

pytestmark_posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock only")


def _sample_doc() -> StateDoc:
    fm = StateFrontmatter(
        schema="job-profile",
        schema_version=1,
        last_consolidated=datetime(2026, 5, 7, 18, 20, tzinfo=UTC),
        entries_consolidated=12,
    )
    return StateDoc(
        frontmatter=fm,
        sections=[
            StateSection(title="Skills and strengths", body="Solid Python engineer.\n"),
            StateSection(title="Hard constraints", body="No relocation.\n"),
        ],
    )


def test_render_state_then_read_state_byte_stable_on_canonical_input(tmp_path: Path) -> None:
    doc = _sample_doc()
    rendered = render_state(doc)
    p = tmp_path / "state.md"
    p.write_text(rendered, encoding="utf-8")
    parsed = read_state(p)
    re_rendered = render_state(parsed)
    assert re_rendered == rendered


def test_render_read_idempotent_after_one_normalisation_pass_on_handcrafted_input(
    tmp_path: Path,
) -> None:
    """One normalisation pass converges even on handcrafted, unusual input."""
    handcrafted = (
        "---\n"
        # Different key order, single-quoted strings, hand-typed datetime.
        "entries_consolidated: 3\n"
        "schema_version: 1\n"
        "schema: 'job-profile'\n"
        "last_consolidated: '2026-05-07T18:20:00Z'\n"
        "---\n"
        "\n"
        "# Skills and strengths\n"
        "\n"
        "Some prose.\n"
        "\n"
        "# Hard constraints\n"
        "\n"
        "No relocation.\n"
    )
    p = tmp_path / "state.md"
    p.write_text(handcrafted, encoding="utf-8")
    once = render_state(read_state(p))
    p2 = tmp_path / "state2.md"
    p2.write_text(once, encoding="utf-8")
    twice = render_state(read_state(p2))
    assert once == twice


def test_heading_inside_fenced_code_block_is_body_not_section(tmp_path: Path) -> None:
    doc_text = (
        "---\n"
        "schema: job-profile\n"
        "schema_version: 1\n"
        "entries_consolidated: 0\n"
        "---\n"
        "\n"
        "# Section A\n"
        "\n"
        "Some prose.\n"
        "\n"
        "```\n"
        "# Not a heading\n"
        "```\n"
        "\n"
        "Trailing prose still in section A.\n"
    )
    p = tmp_path / "state.md"
    p.write_text(doc_text, encoding="utf-8")
    doc = read_state(p)
    assert [s.title for s in doc.sections] == ["Section A"]
    body = doc.sections[0].body
    assert "# Not a heading" in body
    assert "Trailing prose still in section A." in body


def test_section_ordering_preserved_round_trip(tmp_path: Path) -> None:
    doc = StateDoc(
        frontmatter=StateFrontmatter(schema="job-profile", schema_version=1),
        sections=[
            StateSection(title="Beta", body="b\n"),
            StateSection(title="Alpha", body="a\n"),
            StateSection(title="Gamma", body="g\n"),
        ],
    )
    p = tmp_path / "state.md"
    p.write_text(render_state(doc), encoding="utf-8")
    parsed = read_state(p)
    assert [s.title for s in parsed.sections] == ["Beta", "Alpha", "Gamma"]


def test_body_verbatim_per_section_with_unusual_whitespace(tmp_path: Path) -> None:
    body = "  indented line\n\n\nblank lines above\n\t\ttabbed\n"
    doc = StateDoc(
        frontmatter=StateFrontmatter(schema="job-profile", schema_version=1),
        sections=[StateSection(title="A", body=body)],
    )
    p = tmp_path / "state.md"
    p.write_text(render_state(doc), encoding="utf-8")
    parsed = read_state(p)
    # Body normalisation strips trailing newlines down to one. Internal
    # whitespace is verbatim; the trailing run collapses to a single ``\n``.
    assert parsed.sections[0].body.rstrip("\n") == body.rstrip("\n")
    assert "  indented line" in parsed.sections[0].body
    assert "\t\ttabbed" in parsed.sections[0].body


def test_preamble_before_first_heading_raises_StateParseError(tmp_path: Path) -> None:
    doc_text = (
        "---\n"
        "schema: job-profile\n"
        "schema_version: 1\n"
        "---\n"
        "\n"
        "Stray prose before any heading.\n"
        "\n"
        "# A heading\n"
        "\n"
        "body\n"
    )
    p = tmp_path / "state.md"
    p.write_text(doc_text, encoding="utf-8")
    with pytest.raises(StateParseError):
        read_state(p)


def test_missing_closing_frontmatter_fence_raises_StateParseError(tmp_path: Path) -> None:
    doc_text = "---\nschema: job-profile\nschema_version: 1\n# Body\n\nno closing fence above\n"
    p = tmp_path / "state.md"
    p.write_text(doc_text, encoding="utf-8")
    with pytest.raises(StateParseError):
        read_state(p)


@pytestmark_posix
def test_write_state_without_lock_raises_assertion_programming_bug(tmp_path: Path) -> None:
    """Programming-bug check: catches the caller forgetting to acquire the topic
    lock at all. NOT a defence against concurrent release between the assertion
    and the atomic rename. Phase 3 may revisit lock/write coupling.
    """
    d = tmp_path / "topic"
    d.mkdir()
    doc = _sample_doc()
    with pytest.raises(AssertionError, match="write_state requires the topic lock"):
        write_state(d / "state.md", doc)


@pytestmark_posix
def test_write_state_under_lock_round_trip(tmp_path: Path) -> None:
    d = tmp_path / "topic"
    d.mkdir()
    doc = _sample_doc()
    with topic_lock(d):
        write_state(d / "state.md", doc)
    parsed = read_state(d / "state.md")
    assert parsed.frontmatter == doc.frontmatter
    assert [s.title for s in parsed.sections] == [s.title for s in doc.sections]


def test_validate_state_matches_schema_titles_in_order_or_raises_StateSchemaMismatchError() -> None:
    schema = Schema(
        name="custom",
        version=1,
        description="d",
        persona="p",
        sections=[
            SchemaSection(id="alpha", title="Alpha", description="a"),
            SchemaSection(id="beta", title="Beta", description="b"),
        ],
    )
    good = StateDoc(
        frontmatter=StateFrontmatter(schema="custom", schema_version=1),
        sections=[
            StateSection(title="Alpha", body="\n"),
            StateSection(title="Beta", body="\n"),
        ],
    )
    validate_state(good, schema)  # no raise

    swapped = StateDoc(
        frontmatter=StateFrontmatter(schema="custom", schema_version=1),
        sections=[
            StateSection(title="Beta", body="\n"),
            StateSection(title="Alpha", body="\n"),
        ],
    )
    with pytest.raises(StateSchemaMismatchError):
        validate_state(swapped, schema)


# ---------------------------------------------------------------------------
# Architectural-rule tests
# ---------------------------------------------------------------------------


def test_state_module_exports_no_update_section() -> None:
    """state.py is whole-document only; no public ``update_section``."""
    assert not hasattr(state_module, "update_section")


def test_validate_state_signature_takes_schema() -> None:
    sig = inspect.signature(validate_state)
    assert list(sig.parameters) == ["doc", "schema"]
