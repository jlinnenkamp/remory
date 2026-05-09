"""Real-CLI integration test (opt-in via REMORY_REAL_CLI=1).

This test is gated and must NOT run on default CI. It exists to enforce
parity between our path-encoding logic in
:mod:`remory.transcripts.encode_cwd_for_claude` and the real ``claude``
CLI's behaviour.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from remory import transcripts
from tests.conftest import real_cli_available

pytestmark = pytest.mark.real_cli


def setup_module(module: Any) -> None:
    del module  # pytest hook signature; argument unused
    available, reason = real_cli_available()
    if not available:
        pytest.skip(reason, allow_module_level=True)


def test_real_claude_transcript_path_encoding_matches_our_locator(tmp_path: Path) -> None:
    cwd = tmp_path / "real_cli_test_topic"
    cwd.mkdir()
    result = subprocess.run(
        ["claude", "-p", "say hi", "--output-format", "json"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(
            f"real claude returned non-zero (likely unauthenticated): {result.stderr[:200]}"
        )
    assert transcripts.locate_latest(cwd) is not None
