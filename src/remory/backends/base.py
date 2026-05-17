"""Backend abstraction for Remory.

This module defines the ``Backend`` Protocol that LLM-driver implementations
satisfy structurally, the typed result models that flow back to callers, and
the exception hierarchy for error translation.

Architectural pins (do not relitigate):

* The Protocol is **non-retrying**. Retries belong to callers, not backends.
* The Protocol is **non-streaming**. Adding streaming later is a *new* method
  (``stream_headless``), not a parameter.
* Auth failures encountered during ``headless`` invocations surface as
  :class:`BackendInvocationError` --- the backend does not attempt to
  discriminate auth failures from other invocation failures.
  The doctor command (Phase 4) is the policy holder for surfacing auth state.
* Backend implementations are **stateless across calls**.

These rules also live, in fuller form, in the :class:`Backend` Protocol
docstring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Backend",
    "BackendAuthError",
    "BackendError",
    "BackendInvocationError",
    "BackendNotFoundError",
    "BackendOutputError",
    "BackendTimeoutError",
    "ChatResult",
    "HeadlessMeta",
    "HeadlessResult",
    "HealthReport",
]


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class BackendError(Exception):
    """Base class for all backend errors."""


class BackendNotFoundError(BackendError):
    """Raised when the backend binary cannot be located on PATH."""


class BackendTimeoutError(BackendError):
    """Raised when a backend invocation exceeds its timeout."""


class BackendInvocationError(BackendError):
    """Subprocess returned a non-zero exit code (and did not time out).

    This is the surface auth failures hit when they occur during
    ``headless()`` --- the real ``claude`` CLI exits non-zero with a message
    in stderr, which the backend cannot reliably distinguish from any other
    invocation failure. Discrimination is the doctor command's job
    (Phase 4), not the backend's.
    """

    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None = None,
        stderr_tail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail


class BackendOutputError(BackendError):
    """Raised for malformed stdout, missing required JSON fields, or
    ``is_error: true`` envelopes from the backend."""


class BackendAuthError(BackendError):
    """Reserved for an explicit auth-probe path.

    **Never raised by Phase 2's** ``chat`` **or** ``headless``. Phase 4's
    doctor command may use this for an auth-probe path. Auth failures
    encountered during ``headless`` surface as
    :class:`BackendInvocationError`; this class exists so a future
    auth-probe API has a distinct exception type to raise.
    """


# ---------------------------------------------------------------------------
# Result models (frozen, extra=forbid)
# ---------------------------------------------------------------------------


class HeadlessMeta(BaseModel):
    """Diagnostic-only auxiliary fields.

    Callers depending on the shape of any field here are wrong; promote a
    field to :class:`HeadlessResult` before depending on it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_envelope: dict[str, Any] | None = None


class HeadlessResult(BaseModel):
    """Structured result of a single ``headless`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    session_id: str | None
    duration_ms: int
    num_turns: int
    stop_reason: str
    meta: HeadlessMeta


class ChatResult(BaseModel):
    """Structured result of a single ``chat`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exit_code: int
    session_id: str | None
    transcript_path: Path | None
    duration_seconds: float
    cwd: Path


class HealthReport(BaseModel):
    """Backend health report.

    ``authenticated=None`` means **unknown**, not False. Phase 4's doctor
    probes; Phase 2's ``health_check`` does not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    binary_present: bool
    binary_path: Path | None = None
    version: str | None = None
    authenticated: bool | None = Field(
        default=None,
        description=(
            "Authentication state. Tri-valued: True means probed and "
            "authenticated, False means probed and not authenticated, "
            "None means UNKNOWN -- the backend did not probe. Phase 2's "
            "health_check always returns None plus a notes entry "
            "'auth not probed'; Phase 4's doctor command is the policy "
            "holder that probes and sets True/False. Treat None as "
            "'unknown', not False."
        ),
    )
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Backend Protocol
# ---------------------------------------------------------------------------


class Backend(Protocol):
    """LLM backend interface. Implementations: ClaudeCodeBackend (default),
    AnthropicAPIBackend (stub).

    Contract rules:

    1. Non-retrying. This Protocol is non-retrying. Callers (the sleep
       orchestrator in Phase 3) wrap ``headless()`` with ``tenacity`` for
       retryable failures: ``BackendTimeoutError`` and
       ``BackendInvocationError`` are retryable; ``BackendOutputError`` is
       not (the orchestrator handles it manually with a stricter prompt for
       stage 1, and propagates it for other stages).

    2. Non-streaming. ``headless()`` is permanently non-streaming for v0.1.
       Adding a streaming variant later requires a new method
       (``stream_headless``), not a parameter on ``headless``.

    3. Auth failures. Auth failures encountered during ``headless``
       invocations surface as ``BackendInvocationError``. The retry-then-
       exhaust pattern means a real auth failure costs 3 retries before
       failing the call. The doctor command (Phase 4) is the policy holder
       for detecting and surfacing auth state to users; the backend does
       not attempt to discriminate auth failures from other invocation
       failures. ``health_check()`` does not probe auth in Phase 2 and
       leaves ``authenticated`` set to ``None`` ("unknown").

    4. Stateless across calls. Backend implementations are stateless across
       calls. No response caching, no cross-call context accumulation, no
       implicit session continuation between ``headless`` invocations. Each
       ``headless`` is a fresh transaction; each ``chat`` starts a fresh
       session unless ``resume=True``.
    """

    def chat(
        self,
        *,
        cwd: Path,
        resume: bool = False,
        agent: str | None = None,
        initial_prompt: str | None = None,
    ) -> ChatResult: ...
    def headless(
        self,
        *,
        prompt: str,
        agent: str | None = None,
        cwd: Path | None = None,
        json_output: bool = False,
        timeout_seconds: int = 600,
    ) -> HeadlessResult: ...
    def health_check(self) -> HealthReport: ...
