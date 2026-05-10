"""Sleep pipeline orchestrator: extract -> merge -> critique.

Public surface:

* :class:`SleepStatus` -- terminal status of a sleep run.
* :class:`SleepError` -- the single failure type the orchestrator raises on
  unrecoverable failures. Carries the offending stage and (when applicable)
  the on-disk backup path so users can recover by hand.
* :class:`SectionOutcome`, :class:`SleepResult` -- structured return.
* :func:`sleep` -- the entry point.

Counter asymmetry (D3) -- intentionally not symmetric:

    state.md.frontmatter.entries_consolidated  -- updated by sleep
    meta.yaml.last_consolidated                -- updated by sleep
    meta.yaml.pending_count                    -- reset to 0 by sleep
    meta.yaml.total_entries                    -- NOT touched by sleep
                                                 (raw entries on disk are
                                                 unchanged in count)

The "entries_consolidated" counter lives in state.md frontmatter, NOT in
meta.yaml. Sleep is the only writer of that field.

Single-backup-pre-merge rule (T1):
    The backup is taken exactly once, before any merge work, and represents
    the pre-sleep state. Do not introduce a second post-merge backup --
    recovery semantics break if there are two candidates to restore from.

lock_timeout semantic (T2):
    ``lock_timeout=0.0`` is non-blocking acquire -- raises
    :class:`~remory.locking.LockBusyError` immediately if another sleep
    holds the topic lock. Zero means fail-fast, not wait-forever.

Schema-drift detection (bidirectional walk):
    The orchestrator walks BOTH the OLD ``state.md`` sections AND the
    schema sections during merge. Iterating only the schema would
    silently drop user-authored content whose section title is not in
    the schema (e.g. a hand-edited "# Notes" section, or a section
    removed in a schema migration). Nothing the user wrote disappears
    silently: each drift section is logged at WARNING level, recorded
    in :attr:`SleepResult.notes`, and surfaces via the
    ``SUCCESS_WITH_WARNINGS`` status. The pre-sleep ``.bak`` is the
    recovery path.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from remory import paths
from remory.atomic import atomic_write_bytes
from remory.backends.base import Backend, BackendError
from remory.locking import topic_lock
from remory.raw import RawEntry, RawStatus, list_raw, mark_status
from remory.schema import SchemaSection
from remory.sleep.critique import CritiqueError, write_review
from remory.sleep.extract import ExtractError, extract
from remory.sleep.merge import MergeError, append_only_merge, merge_section
from remory.state import (
    StateDoc,
    StateFrontmatter,
    StateParseError,
    StateSection,
    read_state,
    render_state,
    write_state,
)
from remory.topic import load_topic, write_meta

__all__ = [
    "SectionOutcome",
    "SleepError",
    "SleepResult",
    "SleepStatus",
    "sleep",
]


_log = logging.getLogger("remory.sleep.orchestrator")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class SleepStatus(StrEnum):
    NO_PENDING = "no_pending"
    SUCCESS = "success"
    SUCCESS_WITH_WARNINGS = "success_with_warnings"


class SleepError(Exception):
    """Single failure type for unrecoverable sleep failures.

    Attributes:
        backup_path: path to the pre-sleep ``state.md.<ts>.bak``, or ``None``
            if the failure occurred before the backup was taken (i.e. during
            extract, or when ``state.md`` is absent).
        state_path: path to ``state.md``. Always set. May be a path that
            does not (yet) exist if ``state.md`` was absent.
        stage: which pipeline stage failed.
        cause: the underlying exception, if any.
    """

    def __init__(
        self,
        message: str,
        *,
        backup_path: Path | None,
        state_path: Path,
        stage: Literal["extract", "merge", "critique"],
        cause: Exception | None,
    ) -> None:
        super().__init__(message)
        self.backup_path = backup_path
        self.state_path = state_path
        self.stage = stage
        self.cause = cause


@dataclass(frozen=True)
class SectionOutcome:
    section_id: str
    candidates_count: int
    action: Literal["llm_merge", "append_only", "skipped_no_candidates"]


@dataclass(frozen=True)
class SleepResult:
    status: SleepStatus
    topic_name: str
    run_id: str
    backup_path: Path | None
    review_path: Path | None
    consolidated_count: int
    section_outcomes: tuple[SectionOutcome, ...]
    notes: tuple[str, ...]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_run_id(now: datetime) -> str:
    """``YYYY-MM-DD-HHMMSS`` UTC; used in log fields and in the backup filename."""
    return now.strftime("%Y-%m-%d-%H%M%S")


def _build_raw_lookup(pending: list[RawEntry]) -> dict[str, RawEntry]:
    """Build the evidence-string -> RawEntry map for append_only_merge.

    The evidence string is the relative POSIX path
    ``raw/<year>/<file>.md`` (D9 regex form), which matches what
    :func:`build_raw_views` synthesises and what the extract LLM is asked
    to cite verbatim.
    """
    lookup: dict[str, RawEntry] = {}
    for entry in pending:
        rel = f"raw/{entry.path.parent.name}/{entry.path.name}"
        lookup[rel] = entry
    return lookup


def _section_text_lookup(
    doc: StateDoc | None,
    schema_sections: list[SchemaSection],
) -> dict[str, str]:
    """Map ``section_id -> current_text`` for the given state document.

    Sections in ``doc`` are matched to schema sections by **title**. A
    schema section without a matching state section gets an empty
    string. Sections present in ``doc`` whose title is NOT in the
    schema (drift) are intentionally not represented here; the
    orchestrator detects them separately via :func:`_drift_sections`
    and surfaces them as warnings rather than dropping them silently.
    """
    if doc is None:
        return {section.id: "" for section in schema_sections}
    by_title: dict[str, str] = {s.title: s.body for s in doc.sections}
    return {section.id: by_title.get(section.title, "") for section in schema_sections}


def _drift_sections(
    doc: StateDoc | None,
    schema_sections: list[SchemaSection],
) -> list[StateSection]:
    """Return state.md sections whose titles are not present in the schema.

    These are dropped from the rewritten ``state.md`` (the schema is
    the source of truth for shape) but their existence is surfaced to
    the user via WARNING-level structured logs and a note in
    :class:`SleepResult`. The pre-sleep ``.bak`` is the recovery path.
    Order is preserved from the source document so warnings have a
    stable, user-meaningful sequence.
    """
    if doc is None:
        return []
    schema_titles = {s.title for s in schema_sections}
    return [s for s in doc.sections if s.title not in schema_titles]


def _build_state_doc(
    *,
    schema_name: str,
    schema_version: int,
    schema_sections: list[SchemaSection],
    section_bodies: Mapping[str, str],
    entries_consolidated: int,
    last_consolidated: datetime,
) -> StateDoc:
    """Assemble a StateDoc from per-section bodies."""
    fm = StateFrontmatter(
        schema=schema_name,
        schema_version=schema_version,
        last_consolidated=last_consolidated,
        entries_consolidated=entries_consolidated,
    )
    sections = [
        StateSection(title=section.title, body=section_bodies.get(section.id, ""))
        for section in schema_sections
    ]
    return StateDoc(frontmatter=fm, sections=sections)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def sleep(
    *,
    topic_dir: Path,
    backend: Backend,
    dry_run: bool = False,
    lock_timeout: float = 0.0,
) -> SleepResult:
    """Run the sleep pipeline for ``topic_dir``.

    Order of operations (lock held for steps 2-9):

    1. Acquire topic lock (``lock_timeout`` semantic per T2).
    2. Load topic + ``list_raw(status=PENDING)``. Empty -> ``NO_PENDING``.
    3. Run extract. Failure -> ``SleepError(stage="extract", backup_path=None)``.
    4. Take backup via ``atomic_write_bytes`` (T1: single backup, pre-merge).
       Skip if ``state.md`` absent (D7).
    5. Walk schema sections in order, building new ``StateDoc``:
       append_only -> mechanical merge; non-append_only with candidates ->
       LLM merge; no candidates -> leave existing section text unchanged.
    6. If ``merge_and_critique`` and not ``dry_run``: render proposed
       ``state.md`` text and call ``write_review``. Failure is non-fatal.
    7. Dry-run: build :class:`SleepResult` with proposed text in ``notes``,
       release lock, return.
    8. Atomically write ``state.md`` with ``entries_consolidated += N``.
    9. ``mark_status`` consolidated entries.
    10. Update ``meta.yaml`` (``last_consolidated``, ``pending_count=0``).
        Release lock; return.

    Counter asymmetry (D3): ``state.md.frontmatter.entries_consolidated``
    accumulates across sleeps; ``meta.yaml.total_entries`` is the count of
    raw files on disk and is NOT touched by sleep.

    Schema-drift detection: the orchestrator walks BOTH the OLD
    ``state.md`` sections AND the schema sections (a bidirectional
    compare); iterating only the schema would silently drop
    user-authored content whose section title is no longer in the
    schema. Nothing the user wrote disappears silently. Each drift
    section is logged at WARNING level (with a 200-char content
    preview), recorded in :attr:`SleepResult.notes`, and turns the run
    into ``SUCCESS_WITH_WARNINGS``. The pre-sleep ``.bak`` is the
    recovery path.
    """
    run_started_at = datetime.now(UTC)
    run_id = _format_run_id(run_started_at)
    state_path = paths.state_file(topic_dir)

    log_extra = {"sleep_run_id": run_id, "topic": topic_dir.name, "stage": "init"}
    _log.info("sleep: starting run", extra=log_extra)

    with topic_lock(topic_dir, timeout=lock_timeout):
        return _sleep_under_lock(
            topic_dir=topic_dir,
            backend=backend,
            dry_run=dry_run,
            run_id=run_id,
            run_started_at=run_started_at,
            state_path=state_path,
        )


def _sleep_under_lock(
    *,
    topic_dir: Path,
    backend: Backend,
    dry_run: bool,
    run_id: str,
    run_started_at: datetime,
    state_path: Path,
) -> SleepResult:
    """Step 2 onward. Caller must hold the topic lock."""
    topic = load_topic(topic_dir)

    # Step 2: pending entries.
    pending = list_raw(topic_dir, status=RawStatus.PENDING)
    if not pending:
        _log.info(
            "sleep: no pending entries; short-circuit",
            extra={"sleep_run_id": run_id, "topic": topic_dir.name, "stage": "init"},
        )
        return SleepResult(
            status=SleepStatus.NO_PENDING,
            topic_name=topic.name,
            run_id=run_id,
            backup_path=None,
            review_path=None,
            consolidated_count=0,
            section_outcomes=(),
            notes=(),
        )

    # Step 3: extract.
    extract_extra = {"sleep_run_id": run_id, "topic": topic_dir.name, "stage": "extract"}
    _log.info("sleep: extract: %d pending entries", len(pending), extra=extract_extra)
    try:
        extract_result = extract(backend=backend, topic=topic, pending=pending)
    except ExtractError as exc:
        _log.error("sleep: extract failed", extra=extract_extra)
        raise SleepError(
            f"extract failed: {exc}",
            backup_path=None,
            state_path=state_path,
            stage="extract",
            cause=exc,
        ) from exc

    # Step 4: backup (T1: single backup, pre-merge). Skipped when dry_run
    # (D8: dry_run writes nothing -- no backup, no state.md change).
    backup_extra = {"sleep_run_id": run_id, "topic": topic_dir.name, "stage": "merge"}
    if dry_run:
        backup_path: Path | None = None
        # Even when skipping the backup, we still need to detect the
        # "exists-but-unreadable" case (D7 second arm) so a dry run does not
        # silently sail past corruption. Probe by attempting to open.
        if state_path.exists():
            try:
                state_path.read_bytes()
            except OSError as exc:
                raise SleepError(
                    f"could not read state.md: {exc}",
                    backup_path=None,
                    state_path=state_path,
                    stage="merge",
                    cause=exc,
                ) from exc
    else:
        backup_path = _take_backup(
            state_path=state_path,
            topic_dir=topic_dir,
            run_id=run_id,
            log_extra=backup_extra,
        )

    # Read existing state (if any) to seed current section text.
    existing_doc: StateDoc | None = None
    if state_path.exists():
        try:
            existing_doc = read_state(state_path)
        except StateParseError as exc:
            raise SleepError(
                f"could not read existing state.md: {exc}",
                backup_path=backup_path,
                state_path=state_path,
                stage="merge",
                cause=exc,
            ) from exc

    # Step 5: walk schema sections; build per-section bodies.
    schema_sections = list(topic.schema.sections)
    raw_lookup = _build_raw_lookup(pending)
    current_bodies = _section_text_lookup(existing_doc, schema_sections)

    # Bidirectional compare: detect state.md sections whose title is
    # not in the schema (drift). Nothing the user wrote disappears
    # silently -- emit one WARNING per drift section, with a content
    # preview, and surface via notes + SUCCESS_WITH_WARNINGS.
    drift = _drift_sections(existing_doc, schema_sections)
    drift_notes: list[str] = []
    for ds in drift:
        preview = ds.body[:200]
        _log.warning(
            "sleep: dropping drift section %r (not in schema)",
            ds.title,
            extra={
                "sleep_run_id": run_id,
                "topic": topic_dir.name,
                "stage": "merge",
                "drift_section_title": ds.title,
                "dropped_content_preview": preview,
            },
        )
        drift_notes.append(f"dropped drift section '{ds.title}' (not in schema; see logs)")

    new_bodies: dict[str, str] = {}
    section_outcomes: list[SectionOutcome] = []
    revise = topic.schema.sleep.default_depth == "merge_and_critique"
    persona = topic.schema.persona
    knobs = topic.meta.knobs

    for section in schema_sections:
        candidates = extract_result.for_section(section.id)
        current = current_bodies[section.id]
        per_section_extra = {
            "sleep_run_id": run_id,
            "topic": topic_dir.name,
            "stage": "merge",
            "section_id": section.id,
        }
        if section.append_only:
            if candidates:
                _log.info(
                    "sleep: append_only_merge for %s (%d candidates)",
                    section.id,
                    len(candidates),
                    extra=per_section_extra,
                )
                try:
                    new_bodies[section.id] = append_only_merge(
                        section=section,
                        current_text=current,
                        candidates=candidates,
                        raw_lookup=raw_lookup,
                    )
                except MergeError as exc:
                    raise SleepError(
                        f"append_only_merge failed for section {section.id!r}: {exc}",
                        backup_path=backup_path,
                        state_path=state_path,
                        stage="merge",
                        cause=exc,
                    ) from exc
                section_outcomes.append(
                    SectionOutcome(
                        section_id=section.id,
                        candidates_count=len(candidates),
                        action="append_only",
                    )
                )
            else:
                new_bodies[section.id] = current
                section_outcomes.append(
                    SectionOutcome(
                        section_id=section.id,
                        candidates_count=0,
                        action="skipped_no_candidates",
                    )
                )
            continue

        if not candidates:
            new_bodies[section.id] = current
            section_outcomes.append(
                SectionOutcome(
                    section_id=section.id,
                    candidates_count=0,
                    action="skipped_no_candidates",
                )
            )
            continue

        _log.info(
            "sleep: merge_section for %s (%d candidates, revise=%s)",
            section.id,
            len(candidates),
            revise,
            extra=per_section_extra,
        )
        try:
            new_bodies[section.id] = merge_section(
                backend=backend,
                section=section,
                current_text=current,
                candidates=list(candidates),
                persona=persona,
                knobs=knobs,
                revise=revise,
            )
        except (BackendError, MergeError) as exc:
            _log.error(
                "sleep: merge failed for section %s: %s",
                section.id,
                exc,
                extra=per_section_extra,
            )
            raise SleepError(
                f"merge_section failed for section {section.id!r}: {exc}",
                backup_path=backup_path,
                state_path=state_path,
                stage="merge",
                cause=exc,
            ) from exc
        section_outcomes.append(
            SectionOutcome(
                section_id=section.id,
                candidates_count=len(candidates),
                action="llm_merge",
            )
        )

    # Build the proposed StateDoc -- frontmatter takes accumulated count plus
    # this run's N. If state.md was absent the prior count is 0.
    prior_consolidated = (
        existing_doc.frontmatter.entries_consolidated if existing_doc is not None else 0
    )
    new_doc = _build_state_doc(
        schema_name=topic.schema.name,
        schema_version=topic.schema.version,
        schema_sections=schema_sections,
        section_bodies=new_bodies,
        entries_consolidated=prior_consolidated + len(pending),
        last_consolidated=run_started_at,
    )
    proposed_text = render_state(new_doc)

    # Step 6: critique (only if merge_and_critique AND not dry_run).
    # Drift notes come first (bidirectional walk above); critique-skip
    # notes (if any) come after. Pinned ordering matters for tests.
    notes: list[str] = list(drift_notes)
    review_path: Path | None = None
    status = SleepStatus.SUCCESS_WITH_WARNINGS if drift_notes else SleepStatus.SUCCESS
    if revise and not dry_run:
        review_path_candidate = paths.review_file(topic_dir)
        critique_extra = {
            "sleep_run_id": run_id,
            "topic": topic_dir.name,
            "stage": "critique",
        }
        try:
            write_review(
                backend=backend,
                topic=topic,
                state_md_text=proposed_text,
                review_path=review_path_candidate,
            )
        except CritiqueError as exc:
            _log.warning(
                "sleep: critique failed (non-fatal): %s",
                exc,
                extra=critique_extra,
            )
            notes.append(f"critique failed: {exc}")
            status = SleepStatus.SUCCESS_WITH_WARNINGS
        else:
            review_path = review_path_candidate

    # Step 7: dry-run short-circuit.
    if dry_run:
        notes.insert(0, "DRY-RUN: no files written")
        notes.append(f"proposed_state_md:\n{proposed_text}")
        return SleepResult(
            status=status,
            topic_name=topic.name,
            run_id=run_id,
            backup_path=backup_path,
            review_path=None,
            consolidated_count=len(pending),
            section_outcomes=tuple(section_outcomes),
            notes=tuple(notes),
        )

    # Step 8: atomic write of state.md.
    write_state(state_path, new_doc)

    # Step 9: mark consolidated.
    mark_status(pending, RawStatus.CONSOLIDATED)

    # Step 10: meta.yaml update.
    new_meta = topic.meta.model_copy(
        update={
            "last_consolidated": run_started_at,
            "pending_count": 0,
            # total_entries intentionally NOT updated (D3).
        }
    )
    write_meta(topic_dir, new_meta)

    return SleepResult(
        status=status,
        topic_name=topic.name,
        run_id=run_id,
        backup_path=backup_path,
        review_path=review_path,
        consolidated_count=len(pending),
        section_outcomes=tuple(section_outcomes),
        notes=tuple(notes),
    )


def _take_backup(
    *,
    state_path: Path,
    topic_dir: Path,
    run_id: str,
    log_extra: Mapping[str, str],
) -> Path | None:
    """Take a single pre-merge backup of ``state.md``.

    Returns the backup path on success, ``None`` if ``state.md`` was absent
    (D7 first arm). Raises :class:`SleepError` if ``state.md`` exists but is
    unreadable (D7 second arm).
    """
    if not state_path.exists():
        _log.info(
            "sleep: state.md absent, skipping backup",
            extra=log_extra,
        )
        return None

    backups_dir = paths.backups_dir(topic_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backups_dir / f"state.md.{run_id}.bak"
    try:
        data = state_path.read_bytes()
    except OSError as exc:
        raise SleepError(
            f"could not read state.md for backup: {exc}",
            backup_path=None,
            state_path=state_path,
            stage="merge",
            cause=exc,
        ) from exc
    atomic_write_bytes(backup_path, data)
    _log.info(
        "sleep: wrote backup %s",
        backup_path,
        extra={**log_extra, "backup_path": str(backup_path)},
    )
    return backup_path
