"""Importable in-process Backend fake for unit tests.

See ``tests/fakes/__init__.py`` for the strict layer rule that separates
this from ``fake_claude``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from pathlib import Path

from remory.backends.base import (
    BackendError,
    BackendInvocationError,
    ChatResult,
    HeadlessMeta,
    HeadlessResult,
    HealthReport,
)


class FakeBackend:
    """Backend implementation backed by canned results.

    ``headless_results`` is consumed in FIFO order; each item is either a
    :class:`HeadlessResult` (returned to the caller) or an
    :class:`Exception` instance (raised at the caller).

    ``chat_result`` and ``health_report`` configure the return values of
    :meth:`chat` and :meth:`health_check` respectively.
    """

    def __init__(
        self,
        *,
        headless_results: Iterable[HeadlessResult | Exception] = (),
        chat_result: ChatResult | None = None,
        health_report: HealthReport | None = None,
    ) -> None:
        self._queue: deque[HeadlessResult | Exception] = deque(headless_results)
        self._chat_result = chat_result
        self._health_report = health_report
        self.headless_calls: list[dict[str, object]] = []
        self.chat_calls: list[dict[str, object]] = []

    def chat(self, *, cwd: Path, resume: bool = False, agent: str | None = None) -> ChatResult:
        self.chat_calls.append({"cwd": cwd, "resume": resume, "agent": agent})
        if self._chat_result is None:
            raise AssertionError("FakeBackend.chat called without a configured chat_result")
        return self._chat_result

    def headless(
        self,
        *,
        prompt: str,
        agent: str | None = None,
        cwd: Path | None = None,
        json_output: bool = False,
        timeout_seconds: int = 600,
    ) -> HeadlessResult:
        self.headless_calls.append(
            {
                "prompt": prompt,
                "agent": agent,
                "cwd": cwd,
                "json_output": json_output,
                "timeout_seconds": timeout_seconds,
            }
        )
        if not self._queue:
            raise AssertionError("FakeBackend.headless called with empty result queue")
        item = self._queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    def health_check(self) -> HealthReport:
        if self._health_report is None:
            raise AssertionError(
                "FakeBackend.health_check called without a configured health_report"
            )
        return self._health_report

    @classmethod
    def with_auth_failure(cls, *, stderr_tail: str) -> FakeBackend:
        """Build a FakeBackend whose first ``headless`` raises an auth-shaped error.

        Convenience for the Phase 4 doctor's auth-probe classification
        tests. The resulting backend raises
        :class:`BackendInvocationError` with the supplied
        ``stderr_tail`` (so the doctor's substring-matching auth-probe
        can classify it as FAIL or WARN depending on keywords).
        """
        return cls(
            headless_results=(
                BackendInvocationError(
                    "claude exited with code 1",
                    exit_code=1,
                    stderr_tail=stderr_tail,
                ),
            ),
        )

    @classmethod
    def with_letter_text(cls, text: str) -> FakeBackend:
        """Build a FakeBackend whose first ``headless`` returns ``text``.

        Convenience for the Phase 5 wizard letter-step tests. The
        resulting backend yields a :class:`HeadlessResult` with the
        supplied text and otherwise-canned diagnostic fields. Wizard
        callers don't depend on session_id / duration_ms / etc., so
        the values here are placeholders.
        """
        return cls(
            headless_results=(
                HeadlessResult(
                    text=text,
                    session_id="fake-letter-session",
                    duration_ms=1,
                    num_turns=1,
                    stop_reason="end_turn",
                    meta=HeadlessMeta(raw_envelope=None),
                ),
            ),
        )

    @classmethod
    def with_letter_failure(
        cls,
        exc_class: type[BackendError],
        *,
        message: str = "forced failure for test",
        exit_code: int | None = None,
        stderr_tail: str | None = None,
    ) -> FakeBackend:
        """Build a FakeBackend whose first ``headless`` raises ``exc_class(...)``.

        Convenience for the Phase 5 wizard letter-step tests covering
        the D1 fallback path. ``exc_class`` may be any
        :class:`BackendError` subclass; ``message`` is the
        first-positional argument. For
        :class:`BackendInvocationError` callers can supply
        ``exit_code`` and ``stderr_tail``; the kwargs are silently
        ignored for other subclasses (the ``BackendError`` base only
        takes a message).
        """
        if exc_class is BackendInvocationError:
            exc: BackendError = BackendInvocationError(
                message,
                exit_code=exit_code,
                stderr_tail=stderr_tail,
            )
        else:
            exc = exc_class(message)
        return cls(headless_results=(exc,))
