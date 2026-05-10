"""Stage 2 of the sleep pipeline: per-section merge.

Two paths:

* :func:`merge_section` -- LLM-driven merge of a non-append-only section.
  Section isolation is enforced at the prompt layer (one
  :class:`~remory.sleep.prompts.MergeContext` -> one section).
* :func:`append_only_merge` -- pure mechanical append for ``append_only``
  sections, no LLM call. Implements the wire format from D4.

Schema-drift detection lives in the orchestrator, not here. The
orchestrator walks BOTH the OLD ``state.md`` sections AND the schema
sections; iterating only the schema would silently drop user-authored
content whose title is not in the schema. Nothing the user wrote
disappears silently -- drift sections are surfaced via WARNING log,
``SleepResult.notes`` entries, and ``SUCCESS_WITH_WARNINGS`` status.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC

from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from remory.backends.base import (
    Backend,
    BackendInvocationError,
    BackendTimeoutError,
)
from remory.raw import RawEntry
from remory.schema import SchemaSection
from remory.sleep.extract import ExtractCandidate
from remory.sleep.prompts import (
    MergeContext,
    render_merge_prompt,
    render_merge_revise_prompt,
)
from remory.topic import Knobs

__all__ = [
    "MergeError",
    "append_only_merge",
    "merge_section",
]


_log = logging.getLogger("remory.sleep.merge")


class MergeError(Exception):
    """Raised when merge fails irrecoverably.

    Includes precondition violations (e.g. ``section.append_only is True``,
    no candidates), backend errors after retries are exhausted, and
    :func:`append_only_merge`'s "unknown evidence string" case.
    """


# ---------------------------------------------------------------------------
# LLM-driven merge
# ---------------------------------------------------------------------------


def _invoke_with_retries(backend: Backend, *, prompt: str) -> str:
    retrying = Retrying(
        retry=retry_if_exception_type((BackendTimeoutError, BackendInvocationError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.0, min=1.0, max=8.0),
        reraise=True,
    )
    for attempt in retrying:
        with attempt:
            result = backend.headless(prompt=prompt, agent="merger", json_output=False)
            return result.text
    raise RuntimeError("unreachable: tenacity Retrying exited loop without returning or raising")


def merge_section(
    *,
    backend: Backend,
    section: SchemaSection,
    current_text: str,
    candidates: Sequence[ExtractCandidate],
    persona: str,
    knobs: Knobs,
    revise: bool,
) -> str:
    """Run stage 2 for a single non-append-only section.

    Preconditions:
        * ``section.append_only is False``.
        * ``candidates`` is non-empty.

    On ``revise=True`` a second backend call is made with the
    generate-then-revise prompt; the second output replaces the first.

    Backend errors during merge bubble up as :class:`BackendInvocationError`
    or :class:`BackendOutputError`; the orchestrator translates them into a
    :class:`SleepError` with ``stage="merge"``. ``BackendOutputError`` is
    not retried here -- there is no stricter-prompt sub-policy for merge.
    """
    if section.append_only:
        raise MergeError(
            f"merge_section precondition violated: section {section.id!r} is append_only"
        )
    if not candidates:
        raise MergeError(
            f"merge_section precondition violated: no candidates for section {section.id!r}"
        )

    ctx = MergeContext(
        section=section,
        current_section_text=current_text,
        candidates=tuple(candidates),
        persona=persona,
        knobs=knobs,
    )
    prompt = render_merge_prompt(ctx)
    draft = _invoke_with_retries(backend, prompt=prompt)
    if not revise:
        return draft

    revise_prompt = render_merge_revise_prompt(ctx, draft=draft)
    return _invoke_with_retries(backend, prompt=revise_prompt)


# ---------------------------------------------------------------------------
# Append-only mechanical merge
# ---------------------------------------------------------------------------


def _normalise_text(text: str) -> str:
    """Multi-line -> single-line per D4.5: replace newlines with ``' '`` and strip.

    Order pinned by spec: ``text.replace('\\n', ' ').replace('\\r', ' ').strip()``.
    Sequential ``\\r\\n`` collapses to two spaces; that is the wire contract.
    """
    return text.replace("\n", " ").replace("\r", " ").strip()


def _format_bullet(date_iso: str, text: str, evidence: str) -> str:
    """Wire format from D4: ``- YYYY-MM-DD: {text} (evidence: {path})``."""
    return f"- {date_iso}: {text} (evidence: {evidence})"


def append_only_merge(
    *,
    section: SchemaSection,
    current_text: str,
    candidates: Sequence[ExtractCandidate],
    raw_lookup: Mapping[str, RawEntry],
) -> str:
    """Mechanical append for an ``append_only`` section.

    Pure: no I/O, no LLM. Implements the wire format pinned by D4:

    1. Date is zero-padded ``YYYY-MM-DD`` derived from
       ``RawEntry.frontmatter.created`` converted to UTC.
    2. ``{path}`` uses POSIX forward slashes (the input ``evidence`` string
       already conforms via D9 regex; pass-through).
    3. Bullets are ordered by ``frontmatter.created`` ascending when multiple
       candidates land in this section in one run.
    4. Empty ``{text}`` after normalisation is skipped silently with a debug
       log (extract-side bug; producer stays pure).
    5. Multi-line text is collapsed to a single line at the producer.

    ``raw_lookup`` maps the evidence string (e.g. ``raw/2026/2026-05-09-0930.md``)
    to its :class:`RawEntry`. An evidence string with no matching entry
    raises :class:`MergeError`.

    The ``current_text`` is preserved verbatim above the new bullets; if it
    is non-empty it is followed by a blank-line separator before the new
    bullets so the section stays valid markdown.
    """
    # Resolve every candidate to its RawEntry first, so an unknown evidence
    # raises before we start emitting bullets.
    enriched: list[tuple[ExtractCandidate, RawEntry]] = []
    for candidate in candidates:
        entry = raw_lookup.get(candidate.evidence)
        if entry is None:
            raise MergeError(
                f"append_only_merge: unknown evidence {candidate.evidence!r} in section "
                f"{section.id!r}; not in raw_lookup"
            )
        enriched.append((candidate, entry))

    # Sort by created ascending (D4.3).
    enriched.sort(key=lambda pair: pair[1].frontmatter.created)

    bullets: list[str] = []
    for candidate, entry in enriched:
        text_normalised = _normalise_text(candidate.text)
        if not text_normalised:
            _log.debug(
                "append_only_merge: skipping empty-text candidate",
                extra={
                    "section_id": section.id,
                    "evidence": candidate.evidence,
                },
            )
            continue
        # D4.1: zero-padded UTC date from RawEntry.frontmatter.created.
        created = entry.frontmatter.created
        if created.tzinfo is None:
            # Defensive: created is expected to carry tzinfo. If it does not,
            # interpret as UTC for the date conversion -- never silently let
            # local-clock skew change the date.
            date_iso = created.strftime("%Y-%m-%d")
        else:
            date_iso = created.astimezone(UTC).strftime("%Y-%m-%d")
        bullets.append(_format_bullet(date_iso, text_normalised, candidate.evidence))

    if not bullets:
        # No bullets to add (every candidate had empty text). Preserve current
        # text verbatim.
        return current_text

    # Compose: existing body (verbatim) + blank-line separator + new bullets +
    # trailing newline. If existing body already ends with a newline, we still
    # want a blank line before the bullets, so we ensure exactly one ``\n``
    # between content and the leading ``-``.
    new_block = "\n".join(bullets) + "\n"
    if current_text == "":
        return new_block
    if current_text.endswith("\n\n"):
        # Already has the blank-line separator.
        return current_text + new_block
    if current_text.endswith("\n"):
        return current_text + "\n" + new_block
    return current_text + "\n\n" + new_block
