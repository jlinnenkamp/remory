"""Stub for contributors. Not exercised by default test runs.

Methods raise :class:`NotImplementedError` (not :class:`BackendError`) ---
programmer-error vs runtime-failure are different categories. Wiring this
backend up is out of scope for v0.1; it ships as a reference for
contributors interested in metered API access.
"""

from __future__ import annotations

from pathlib import Path

from remory.backends.base import ChatResult, HeadlessResult, HealthReport

__all__ = ["AnthropicAPIBackend"]


_STUB_MESSAGE = "AnthropicAPIBackend stub; not wired in v0.1"


class AnthropicAPIBackend:
    """Reference stub for direct Anthropic Messages API integration.

    Constructor takes an optional API key and stores it; otherwise does
    nothing. All methods raise :class:`NotImplementedError`.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def chat(self, *, cwd: Path, resume: bool = False) -> ChatResult:
        raise NotImplementedError(_STUB_MESSAGE)

    def headless(
        self,
        *,
        prompt: str,
        agent: str | None = None,
        cwd: Path | None = None,
        json_output: bool = False,
        timeout_seconds: int = 600,
    ) -> HeadlessResult:
        raise NotImplementedError(_STUB_MESSAGE)

    def health_check(self) -> HealthReport:
        raise NotImplementedError(_STUB_MESSAGE)
