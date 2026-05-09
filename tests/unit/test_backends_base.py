"""Unit tests for ``remory.backends.base``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from remory.backends.base import (
    BackendAuthError,
    BackendError,
    BackendInvocationError,
    BackendNotFoundError,
    BackendOutputError,
    BackendTimeoutError,
    HeadlessMeta,
    HeadlessResult,
    HealthReport,
)


def test_exception_hierarchy_inheritance() -> None:
    for cls in (
        BackendNotFoundError,
        BackendTimeoutError,
        BackendInvocationError,
        BackendOutputError,
        BackendAuthError,
    ):
        assert issubclass(cls, BackendError)


def test_models_are_frozen() -> None:
    result = HeadlessResult(
        text="hi",
        session_id="s",
        duration_ms=1,
        num_turns=1,
        stop_reason="end_turn",
        meta=HeadlessMeta(raw_envelope=None),
    )
    with pytest.raises(ValidationError):
        result.text = "mutated"  # pyright: ignore[reportAttributeAccessIssue]


def test_health_report_authenticated_default_is_None() -> None:
    report = HealthReport(binary_present=True)
    assert report.authenticated is None


def test_health_report_authenticated_field_description_documents_unknown_semantic() -> None:
    field_info = HealthReport.model_fields["authenticated"]
    description = field_info.description or ""
    assert "unknown" in description.lower(), description


def test_headless_meta_raw_envelope_default_is_None() -> None:
    meta = HeadlessMeta()
    assert meta.raw_envelope is None
