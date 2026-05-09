"""Unit tests for ``tests.fakes.fake_backend.FakeBackend``."""

from __future__ import annotations

import pytest

from remory.backends.base import (
    BackendInvocationError,
    HeadlessMeta,
    HeadlessResult,
)
from tests.fakes.fake_backend import FakeBackend


def _make_result(text: str) -> HeadlessResult:
    return HeadlessResult(
        text=text,
        session_id="s",
        duration_ms=1,
        num_turns=1,
        stop_reason="end_turn",
        meta=HeadlessMeta(raw_envelope=None),
    )


def test_fake_backend_headless_returns_canned_result() -> None:
    canned = _make_result("hello")
    fake = FakeBackend(headless_results=[canned])
    out = fake.headless(prompt="anything")
    assert out is canned
    assert fake.headless_calls[0]["prompt"] == "anything"


def test_fake_backend_headless_raises_canned_exception() -> None:
    fake = FakeBackend(
        headless_results=[BackendInvocationError("nope", exit_code=1)],
    )
    with pytest.raises(BackendInvocationError):
        fake.headless(prompt="x")
