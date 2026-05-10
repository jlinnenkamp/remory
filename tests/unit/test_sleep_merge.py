"""Unit tests for ``remory.sleep.merge``."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from remory.backends.base import HeadlessMeta, HeadlessResult
from remory.locking import topic_lock
from remory.raw import RawEntry, RawFrontmatter, RawSource, RawStatus, read_raw, write_raw
from remory.schema import SchemaSection, load_builtin
from remory.sleep.extract import ExtractCandidate
from remory.sleep.merge import MergeError, append_only_merge, merge_section
from remory.topic import Knobs
from tests.fakes.fake_backend import FakeBackend

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock only")


_KNOBS = Knobs(tone="warm", strictness="balanced")


def _result(text: str) -> HeadlessResult:
    return HeadlessResult(
        text=text,
        session_id="s",
        duration_ms=1,
        num_turns=1,
        stop_reason="end_turn",
        meta=HeadlessMeta(raw_envelope=None),
    )


def _skills_section() -> SchemaSection:
    return load_builtin("job-profile").sections[0]


def _evidence_log_section() -> SchemaSection:
    schema = load_builtin("job-profile")
    return next(s for s in schema.sections if s.id == "evidence_log")


def _make_pending_raw(topic_dir: Path, when: datetime) -> RawEntry:
    fm = RawFrontmatter(
        created=when,
        source=RawSource.CHAT,
        status=RawStatus.PENDING,
        session_id="s",
    )
    with topic_lock(topic_dir):
        path = write_raw(topic_dir, frontmatter=fm, body="b")
    return read_raw(path)


def _lookup(entries: list[RawEntry]) -> dict[str, RawEntry]:
    out: dict[str, RawEntry] = {}
    for e in entries:
        rel = f"raw/{e.path.parent.name}/{e.path.name}"
        out[rel] = e
    return out


# ---------------------------------------------------------------------------
# merge_section
# ---------------------------------------------------------------------------


def test_merge_section_passes_only_one_section_text_to_backend() -> None:
    backend = FakeBackend(headless_results=[_result("rewritten body\n")])
    section = _skills_section()
    candidate = ExtractCandidate(text="x", evidence="raw/2026/2026-05-09-0930.md")
    out = merge_section(
        backend=backend,
        section=section,
        current_text="OLD_BODY_MARKER",
        candidates=[candidate],
        persona="P",
        knobs=_KNOBS,
        revise=False,
    )
    assert out == "rewritten body\n"
    assert len(backend.headless_calls) == 1
    prompt = backend.headless_calls[0]["prompt"]
    assert isinstance(prompt, str)
    # The prompt contains THIS section's marker and not any other section's
    # title -- section isolation enforced via MergeContext.
    assert "OLD_BODY_MARKER" in prompt
    assert "Skills and strengths" in prompt
    assert "Hard constraints" not in prompt
    assert "Evidence log" not in prompt


def test_merge_section_revise_invokes_backend_twice() -> None:
    backend = FakeBackend(
        headless_results=[
            _result("draft body\n"),
            _result("revised body\n"),
        ]
    )
    section = _skills_section()
    candidate = ExtractCandidate(text="x", evidence="raw/2026/2026-05-09-0930.md")
    out = merge_section(
        backend=backend,
        section=section,
        current_text="",
        candidates=[candidate],
        persona="P",
        knobs=_KNOBS,
        revise=True,
    )
    assert out == "revised body\n"
    assert len(backend.headless_calls) == 2
    second = backend.headless_calls[1]["prompt"]
    assert isinstance(second, str)
    # The revise prompt sees the draft.
    assert "draft body" in second


def test_merge_section_no_candidates_precondition_violated_raises() -> None:
    backend = FakeBackend(headless_results=[])
    with pytest.raises(MergeError, match="no candidates"):
        merge_section(
            backend=backend,
            section=_skills_section(),
            current_text="",
            candidates=[],
            persona="P",
            knobs=_KNOBS,
            revise=False,
        )
    assert backend.headless_calls == []


def test_merge_section_append_only_precondition_violated_raises() -> None:
    backend = FakeBackend(headless_results=[])
    candidate = ExtractCandidate(text="x", evidence="raw/2026/2026-05-09-0930.md")
    with pytest.raises(MergeError, match="append_only"):
        merge_section(
            backend=backend,
            section=_evidence_log_section(),
            current_text="",
            candidates=[candidate],
            persona="P",
            knobs=_KNOBS,
            revise=False,
        )
    assert backend.headless_calls == []


# ---------------------------------------------------------------------------
# append_only_merge
# ---------------------------------------------------------------------------


def test_append_only_merge_format_includes_iso_date_and_evidence_label(
    tmp_path: Path,
) -> None:
    raw = _make_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    candidate = ExtractCandidate(
        text="solid finding",
        evidence=f"raw/{raw.path.parent.name}/{raw.path.name}",
    )
    out = append_only_merge(
        section=_evidence_log_section(),
        current_text="",
        candidates=[candidate],
        raw_lookup=_lookup([raw]),
    )
    assert out.startswith("- 2026-05-09: solid finding (evidence: raw/2026/2026-05-09-0930.md)")


def test_append_only_merge_date_uses_utc_not_local_tz(tmp_path: Path) -> None:
    """``created`` in a non-UTC tz where local day != UTC day must render the UTC date.

    A naive implementation that calls ``created.date()`` would render the local
    day. The producer is contracted to call ``astimezone(UTC).strftime(...)``.
    """
    # 2026-05-09 01:30 in Asia/Tokyo (+09:00) -> 2026-05-08 16:30 UTC.
    tokyo = timezone(timedelta(hours=9))
    raw = _make_pending_raw(tmp_path, datetime(2026, 5, 9, 1, 30, tzinfo=tokyo))
    candidate = ExtractCandidate(
        text="datebound",
        evidence=f"raw/{raw.path.parent.name}/{raw.path.name}",
    )
    out = append_only_merge(
        section=_evidence_log_section(),
        current_text="",
        candidates=[candidate],
        raw_lookup=_lookup([raw]),
    )
    # UTC date is May 8 even though local-tz date is May 9.
    assert "- 2026-05-08: datebound" in out
    assert "2026-05-09: datebound" not in out


def test_append_only_merge_orders_bullets_by_created_ascending(tmp_path: Path) -> None:
    later = _make_pending_raw(tmp_path, datetime(2026, 5, 9, 18, 0, tzinfo=UTC))
    earlier = _make_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    cand_later = ExtractCandidate(
        text="later",
        evidence=f"raw/{later.path.parent.name}/{later.path.name}",
    )
    cand_earlier = ExtractCandidate(
        text="earlier",
        evidence=f"raw/{earlier.path.parent.name}/{earlier.path.name}",
    )
    # Pass in reverse order; expect output sorted by created ascending.
    out = append_only_merge(
        section=_evidence_log_section(),
        current_text="",
        candidates=[cand_later, cand_earlier],
        raw_lookup=_lookup([later, earlier]),
    )
    # Earlier bullet appears first.
    earlier_idx = out.index("earlier")
    later_idx = out.index("later")
    assert earlier_idx < later_idx


def test_append_only_merge_skips_empty_text_with_debug_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = _make_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    # The candidate's text is "   " (whitespace), which normalises to "". The
    # producer is pure: it skips silently with a DEBUG log. We have to use a
    # dummy non-whitespace text for the model validation (min_length=1) and
    # then go around model validation -- or use a single-character whitespace
    # that the regex permits and the model accepts. ``Field(min_length=1)``
    # counts whitespace as length-1, so " " is acceptable to Pydantic but
    # normalises to "" at the producer.
    candidate = ExtractCandidate(
        text=" ",
        evidence=f"raw/{raw.path.parent.name}/{raw.path.name}",
    )
    with caplog.at_level(logging.DEBUG, logger="remory.sleep.merge"):
        out = append_only_merge(
            section=_evidence_log_section(),
            current_text="existing\n",
            candidates=[candidate],
            raw_lookup=_lookup([raw]),
        )
    # No bullet was added; current_text preserved.
    assert out == "existing\n"
    # A debug log fired.
    assert any("skipping empty-text candidate" in rec.getMessage() for rec in caplog.records)


def test_append_only_merge_strips_multiline_text_to_single_line(tmp_path: Path) -> None:
    raw = _make_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    candidate = ExtractCandidate(
        text="line one\nline two\r\nline three",
        evidence=f"raw/{raw.path.parent.name}/{raw.path.name}",
    )
    out = append_only_merge(
        section=_evidence_log_section(),
        current_text="",
        candidates=[candidate],
        raw_lookup=_lookup([raw]),
    )
    # Output is a single bullet line; no newlines mid-bullet.
    bullet_line = out.rstrip("\n")
    assert "\n" not in bullet_line
    # Per D4.5: ``text.replace('\n', ' ').replace('\r', ' ').strip()``. Sequential
    # ``\r\n`` collapses to two spaces -- pinned by spec, not a bug to fix.
    assert "line one line two  line three" in bullet_line


def test_append_only_merge_uses_posix_path_separators_on_all_platforms(
    tmp_path: Path,
) -> None:
    """The evidence string is POSIX by D9; pass-through must preserve forward slashes."""
    raw = _make_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    candidate = ExtractCandidate(
        text="x",
        evidence=f"raw/{raw.path.parent.name}/{raw.path.name}",
    )
    out = append_only_merge(
        section=_evidence_log_section(),
        current_text="",
        candidates=[candidate],
        raw_lookup=_lookup([raw]),
    )
    assert "raw/2026/2026-05-09-0930.md" in out
    assert "\\" not in out  # no backslashes anywhere


def test_append_only_merge_unknown_evidence_string_raises(tmp_path: Path) -> None:
    raw = _make_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    candidate = ExtractCandidate(
        text="x",
        evidence="raw/2026/missing-file.md",
    )
    with pytest.raises(MergeError, match="unknown evidence"):
        append_only_merge(
            section=_evidence_log_section(),
            current_text="",
            candidates=[candidate],
            raw_lookup=_lookup([raw]),
        )


def test_append_only_merge_preserves_existing_body(tmp_path: Path) -> None:
    raw = _make_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    candidate = ExtractCandidate(
        text="new bullet",
        evidence=f"raw/{raw.path.parent.name}/{raw.path.name}",
    )
    existing = (
        "- 2026-04-01: prior insight (evidence: raw/2026/2026-04-01-1200.md)\n"
        "- 2026-04-15: another (evidence: raw/2026/2026-04-15-0900.md)\n"
    )
    out = append_only_merge(
        section=_evidence_log_section(),
        current_text=existing,
        candidates=[candidate],
        raw_lookup=_lookup([raw]),
    )
    # All prior content survives.
    assert "2026-04-01: prior insight" in out
    assert "2026-04-15: another" in out
    # And the new bullet is present.
    assert "2026-05-09: new bullet" in out
    # The new bullet is at the bottom.
    assert out.index("prior insight") < out.index("new bullet")


def test_append_only_merge_byte_shape_matches_wire_format(tmp_path: Path) -> None:
    """Exact-bytes assertion against the D4 wire format."""
    raw = _make_pending_raw(tmp_path, datetime(2026, 5, 9, 9, 30, tzinfo=UTC))
    candidate = ExtractCandidate(
        text="exact",
        evidence=f"raw/{raw.path.parent.name}/{raw.path.name}",
    )
    out = append_only_merge(
        section=_evidence_log_section(),
        current_text="",
        candidates=[candidate],
        raw_lookup=_lookup([raw]),
    )
    expected = "- 2026-05-09: exact (evidence: raw/2026/2026-05-09-0930.md)\n"
    assert out == expected


def test_append_only_merge_pure_no_io_no_backend(tmp_path: Path) -> None:
    """No backend reference accepted; the function signature is the proof.

    This is a structural check rather than a behaviour check: the function
    cannot accept a Backend because none is in its parameter list. We pin
    the parameter set so accidental future additions surface in review.
    """
    import inspect

    sig = inspect.signature(append_only_merge)
    assert set(sig.parameters) == {"section", "current_text", "candidates", "raw_lookup"}


def test_append_only_merge_zero_pads_single_digit_month_and_day(tmp_path: Path) -> None:
    raw = _make_pending_raw(tmp_path, datetime(2026, 1, 3, 9, 30, tzinfo=UTC))
    candidate = ExtractCandidate(
        text="x",
        evidence=f"raw/{raw.path.parent.name}/{raw.path.name}",
    )
    out = append_only_merge(
        section=_evidence_log_section(),
        current_text="",
        candidates=[candidate],
        raw_lookup=_lookup([raw]),
    )
    assert "- 2026-01-03:" in out
