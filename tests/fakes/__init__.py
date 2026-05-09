"""Test doubles for the Backend layer.

Strict layer rule:

- ``fake_claude`` is a SUBPROCESS-only fake. Used by integration tests.
- ``fake_backend.py``'s ``FakeBackend`` is an IMPORTABLE in-process fake.
  Used by unit tests.

Tests must pick exactly one. Mixing them -- e.g. importing ``FakeBackend``
in an integration test that also puts ``fake_claude`` on PATH -- is a
layering bug. The integration test exists precisely to exercise the
subprocess seam; mocking it out defeats the point.
"""
