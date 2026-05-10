"""Stage 3 of the sleep pipeline: critic writes ``_review.md``.

The critic only **reads** the proposed merged ``state.md`` text (which the
orchestrator passes in-memory); it never modifies ``state.md`` itself, by
construction. The output is atomically written to ``_review.md``.

Failure policy (D2): the orchestrator treats critique failures as
non-fatal and proceeds to write ``state.md`` anyway with status
``SUCCESS_WITH_WARNINGS``. This module surfaces failures via
:class:`CritiqueError`; the orchestrator catches and converts.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from remory.atomic import atomic_write_text
from remory.backends.base import (
    Backend,
    BackendInvocationError,
    BackendOutputError,
    BackendTimeoutError,
)
from remory.sleep.prompts import CritiqueContext, render_critique_prompt
from remory.topic import Topic

__all__ = ["CritiqueError", "write_review"]


_log = logging.getLogger("remory.sleep.critique")


class CritiqueError(Exception):
    """Raised when critique fails (empty output, or backend errors after retries)."""


def _invoke_with_retries(backend: Backend, *, prompt: str) -> str:
    retrying = Retrying(
        retry=retry_if_exception_type((BackendTimeoutError, BackendInvocationError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.0, min=1.0, max=8.0),
        reraise=True,
    )
    for attempt in retrying:
        with attempt:
            result = backend.headless(prompt=prompt, agent="critic", json_output=False)
            return result.text
    raise RuntimeError("unreachable: tenacity Retrying exited loop without returning or raising")


def write_review(
    *,
    backend: Backend,
    topic: Topic,
    state_md_text: str,
    review_path: Path,
) -> None:
    """Run stage 3 and atomically write ``_review.md``.

    Empty/whitespace-only backend output raises :class:`CritiqueError` and
    no file is written.
    """
    ctx = CritiqueContext(
        schema=topic.schema,
        knobs=topic.meta.knobs,
        state_md_text=state_md_text,
    )
    prompt = render_critique_prompt(ctx)
    try:
        text = _invoke_with_retries(backend, prompt=prompt)
    except (BackendInvocationError, BackendOutputError, BackendTimeoutError) as exc:
        raise CritiqueError(f"critique backend failed: {exc}") from exc

    if not text.strip():
        raise CritiqueError("critique backend returned empty/whitespace-only output")

    atomic_write_text(review_path, text)
