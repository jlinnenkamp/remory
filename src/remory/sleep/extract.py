"""Stage 1 of the sleep pipeline: extract candidate updates from raw entries.

Public surface:

* :class:`ExtractCandidate` -- a single section-tagged update, validated
  against the D9 evidence regex.
* :class:`ExtractResult` -- a frozen mapping from section id to candidates.
* :class:`ExtractError` -- raised on unrecoverable extract failure (after
  the retry-with-stricter sub-policy has been exhausted, or on schema-id
  validation failures).
* :func:`extract` -- the entry point.

Retry policy (as of Phase 3):

* Invocation errors (``BackendTimeoutError``, ``BackendInvocationError``)
  are retried by ``tenacity`` up to 3 attempts with exponential backoff.
* ``BackendOutputError`` is **not** retried by ``tenacity``. Instead it is
  hand-handled here: on first occurrence we re-render the prompt with
  ``stricter=True`` and call again (still under the same ``tenacity``
  policy for invocation errors). A second ``BackendOutputError`` raises
  :class:`ExtractError`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from remory.backends.base import (
    Backend,
    BackendInvocationError,
    BackendOutputError,
    BackendTimeoutError,
)
from remory.raw import RawEntry, RawStatus
from remory.sleep.prompts import (
    ExtractContext,
    build_raw_views,
    render_extract_prompt,
)
from remory.topic import Topic

__all__ = [
    "ExtractCandidate",
    "ExtractError",
    "ExtractResult",
    "extract",
]


_log = logging.getLogger("remory.sleep.extract")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ExtractCandidate(BaseModel):
    """One candidate update, tagged to a single section by the orchestrator.

    The ``evidence`` regex is the wire-format guarantee from D9: every
    evidence string is a forward-slash POSIX path relative to the topic
    directory, of the shape ``raw/<year>/<file>.md``. The regex is enforced
    by Pydantic at model-validation time; ``append_only_merge`` relies on
    this invariant.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    evidence: str = Field(min_length=1, pattern=r"^raw/\d{4}/[\w\-]+\.md$")


class ExtractResult(BaseModel):
    """Frozen result of stage 1.

    ``candidates_by_section`` maps section id -> tuple of candidates. Section
    ids must already have been validated against the topic's schema; the
    model itself does not validate them (it has no schema reference).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates_by_section: dict[str, tuple[ExtractCandidate, ...]]

    def for_section(self, section_id: str) -> tuple[ExtractCandidate, ...]:
        """Return candidates for ``section_id``, or empty tuple if none."""
        return self.candidates_by_section.get(section_id, ())


class ExtractError(Exception):
    """Raised when extract fails irrecoverably.

    Causes include: unknown section id in the LLM's response, malformed JSON
    after the retry-with-stricter sub-policy is exhausted, or a precondition
    violation (e.g. empty pending list, non-PENDING entries).
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_payload(text: str, valid_section_ids: frozenset[str]) -> ExtractResult:
    """Parse the backend's text output into an :class:`ExtractResult`.

    Raises :class:`BackendOutputError` for malformed JSON or shape mismatch
    (the orchestrator's sub-policy retries on this with ``stricter=True``).
    Raises :class:`ExtractError` for an unknown section id in the response
    (validating against the schema is the orchestrator's contract; a
    stricter prompt would not fix this).
    """
    try:
        envelope: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BackendOutputError(f"extract response is not valid JSON: {exc}") from exc
    if not isinstance(envelope, dict):
        raise BackendOutputError("extract response is not a JSON object")
    typed: dict[str, object] = cast("dict[str, object]", envelope)

    by_section: dict[str, tuple[ExtractCandidate, ...]] = {}
    for key, value in typed.items():
        if key not in valid_section_ids:
            raise ExtractError(
                f"extract response contains unknown section id {key!r}; "
                f"valid ids are {sorted(valid_section_ids)}"
            )
        if not isinstance(value, list):
            raise BackendOutputError(
                f"section {key!r} in extract response is not a list of candidates"
            )
        items: list[object] = cast("list[object]", value)
        candidates: list[ExtractCandidate] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise BackendOutputError(f"section {key!r}, candidate {idx}: not a JSON object")
            try:
                candidates.append(ExtractCandidate.model_validate(item))
            except ValidationError as exc:
                raise BackendOutputError(
                    f"section {key!r}, candidate {idx}: validation error: {exc}"
                ) from exc
        by_section[key] = tuple(candidates)
    return ExtractResult(candidates_by_section=by_section)


def _invoke_with_invocation_retries(
    backend: Backend,
    *,
    prompt: str,
) -> str:
    """Call ``backend.headless`` once, with tenacity retrying invocation errors.

    ``BackendOutputError`` is **not** in the retry filter; the caller handles
    it via the retry-with-stricter sub-policy.
    """
    retrying = Retrying(
        retry=retry_if_exception_type((BackendTimeoutError, BackendInvocationError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.0, min=1.0, max=8.0),
        reraise=True,
    )
    for attempt in retrying:
        with attempt:
            result = backend.headless(prompt=prompt, agent="extractor", json_output=True)
            return result.text
    # Unreachable: ``reraise=True`` plus ``stop_after_attempt`` either returns
    # in the loop body above or re-raises the underlying exception.
    raise RuntimeError("unreachable: tenacity Retrying exited loop without returning or raising")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract(
    *,
    backend: Backend,
    topic: Topic,
    pending: Sequence[RawEntry],
) -> ExtractResult:
    """Run stage 1: extract candidate updates from ``pending`` raw entries.

    Preconditions:
        * ``pending`` is non-empty.
        * Every entry in ``pending`` has ``frontmatter.status == PENDING``.

    Both preconditions raise :class:`ExtractError`.

    Retry policy:
        * ``BackendTimeoutError`` / ``BackendInvocationError`` -> retried up
          to 3 attempts with exponential backoff (via tenacity).
        * ``BackendOutputError`` -> hand-handled: first occurrence triggers
          one retry with ``stricter=True``; second occurrence raises
          :class:`ExtractError`.
    """
    if not pending:
        raise ExtractError("extract precondition violated: pending must be non-empty")
    non_pending = [e for e in pending if e.frontmatter.status is not RawStatus.PENDING]
    if non_pending:
        names = [str(e.path) for e in non_pending]
        raise ExtractError(
            f"extract precondition violated: {len(non_pending)} entries are not PENDING: {names}"
        )

    valid_section_ids = frozenset(s.id for s in topic.schema.sections)
    raw_views = build_raw_views(tuple(pending))
    ctx = ExtractContext(
        schema=topic.schema,
        knobs=topic.meta.knobs,
        raws=raw_views,
    )

    # First attempt: standard prompt.
    prompt = render_extract_prompt(ctx, stricter=False)
    try:
        text = _invoke_with_invocation_retries(backend, prompt=prompt)
    except RetryError as exc:
        # Should not happen (reraise=True propagates the wrapped exception),
        # but defensively wrap it as an ExtractError so callers do not see a
        # tenacity-shaped exception.
        raise ExtractError(f"extract invocation failed: {exc}") from exc

    try:
        return _parse_payload(text, valid_section_ids)
    except BackendOutputError as first_output_error:
        _log.debug(
            "extract: first parse failed (%s); retrying with stricter prompt",
            first_output_error,
        )
        stricter_prompt = render_extract_prompt(ctx, stricter=True)
        try:
            text2 = _invoke_with_invocation_retries(backend, prompt=stricter_prompt)
        except RetryError as exc:
            raise ExtractError(f"extract invocation failed (stricter pass): {exc}") from exc
        try:
            return _parse_payload(text2, valid_section_ids)
        except BackendOutputError as second_output_error:
            raise ExtractError(
                f"extract response remained malformed after stricter retry: {second_output_error}"
            ) from second_output_error
