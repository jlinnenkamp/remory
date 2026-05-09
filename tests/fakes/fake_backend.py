"""Importable in-process Backend fake for unit tests.

See ``tests/fakes/__init__.py`` for the strict layer rule that separates
this from ``fake_claude``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from pathlib import Path

from remory.backends.base import (
    ChatResult,
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

    def chat(self, *, cwd: Path, resume: bool = False) -> ChatResult:
        self.chat_calls.append({"cwd": cwd, "resume": resume})
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
