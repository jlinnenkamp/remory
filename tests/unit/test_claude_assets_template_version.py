"""Stamp / detect helpers from ``remory.claude_assets``.

Pin the canonical stamp comment, idempotence under re-stamp, and
``detect_version`` semantics for present / absent / garbage cases.

See plan §11.1.
"""

from __future__ import annotations

from remory.claude_assets import (
    PRODUCTION_TEMPLATE_VERSION,
    detect_version,
    stamp_markdown,
)


def test_stamp_markdown_prepends_idempotent_comment() -> None:
    body = "---\nname: foo\n---\nbody text\n"
    once = stamp_markdown(body)
    twice = stamp_markdown(once)
    # Idempotent: re-stamping is a no-op.
    assert once == twice
    assert "<!-- remory: template_version=" in once
    assert detect_version(once) == PRODUCTION_TEMPLATE_VERSION


def test_detect_version_returns_int_when_present() -> None:
    sample = "<!-- remory: template_version=7 -->\nhello\n"
    assert detect_version(sample) == 7


def test_detect_version_returns_none_when_absent() -> None:
    sample = "# heading\nno stamp anywhere\n"
    assert detect_version(sample) is None


def test_detect_version_returns_none_for_garbage_stamp() -> None:
    # An ill-formed stamp comment — text that looks like a stamp but
    # doesn't match the regex (`version=oops` is not an int).
    sample = "<!-- remory: template_version=oops -->\nhello\n"
    assert detect_version(sample) is None
