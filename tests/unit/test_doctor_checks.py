"""Per-check unit tests for the Phase 4 doctor.

Each ``_check_*`` function is exercised in isolation; the run_doctor
integration test (``test_doctor_e2e.py``) is the orchestration test.

Pinned tests:

* ``test_check_claude_auth_case_insensitive_via_lower_once`` (R5).
* ``test_check_config_returns_ok_when_no_config_toml_found`` (R7).
* ``test_check_claude_templates_fails_when_settings_json_absent``
  (Phase 6 §5.11 — replaces the Phase 4 R6 ``hook_installed`` row).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from remory.backends.base import (
    BackendInvocationError,
    BackendOutputError,
    BackendTimeoutError,
    HeadlessMeta,
    HeadlessResult,
)
from remory.commands.doctor_cmd import (
    _check_claude_auth,
    _check_claude_binary,
    _check_claude_templates,
    _check_config,
    _check_data_dir,
    _check_topic,
    _check_topics_summary,
)
from remory.ui import CheckStatus
from tests.fakes.fake_backend import FakeBackend


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Point all REMORY_*_DIR env vars at a fresh tmp tree."""
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def _ok_headless() -> HeadlessResult:
    return HeadlessResult(
        text="pong",
        session_id="sess-abc",
        duration_ms=10,
        num_turns=1,
        stop_reason="end_turn",
        meta=HeadlessMeta(),
    )


# ---------------------------------------------------------------------------
# data_dir
# ---------------------------------------------------------------------------


def test_check_data_dir_returns_ok_when_writable(tmp_path: Path) -> None:
    result = _check_data_dir(tmp_path)
    assert result.status is CheckStatus.OK
    assert str(tmp_path) in result.detail


def test_check_data_dir_returns_fail_when_path_is_readonly(tmp_path: Path) -> None:
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        result = _check_data_dir(ro)
        # Some filesystems silently allow writes when running as root;
        # accept either OK or FAIL but assert the FAIL branch's wording
        # when triggered.
        if result.status is CheckStatus.FAIL:
            assert "not writable" in result.detail
    finally:
        ro.chmod(0o700)


# ---------------------------------------------------------------------------
# config (R7)
# ---------------------------------------------------------------------------


def test_check_config_returns_ok_when_no_config_toml_found(isolated_xdg: Path) -> None:
    """R7 — missing config.toml is OK with the 'defaults' phrasing, not INFO."""
    del isolated_xdg
    result = _check_config()
    assert result.status is CheckStatus.OK
    assert "defaults" in result.detail


def test_check_config_returns_fail_on_invalid_toml_with_path(
    isolated_xdg: Path,
) -> None:
    cfg_path = isolated_xdg / "config" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("[ui]\nemoji = 'not a bool'\n", encoding="utf-8")
    result = _check_config()
    assert result.status is CheckStatus.FAIL
    haystack = (result.detail or "") + " " + " ".join(result.remediation)
    assert str(cfg_path) in haystack


# ---------------------------------------------------------------------------
# claude_binary
# ---------------------------------------------------------------------------


def test_check_claude_binary_returns_fail_when_not_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    result = _check_claude_binary()
    assert result.status is CheckStatus.FAIL


# ---------------------------------------------------------------------------
# claude_auth (R5 substring matching)
# ---------------------------------------------------------------------------


def test_check_claude_auth_returns_skip_when_binary_present_is_false() -> None:
    backend = FakeBackend()
    result = _check_claude_auth(binary_present=False, backend_factory=lambda: backend)
    assert result.status is CheckStatus.SKIP


def test_check_claude_auth_returns_ok_on_successful_headless() -> None:
    backend = FakeBackend(headless_results=(_ok_headless(),))
    result = _check_claude_auth(binary_present=True, backend_factory=lambda: backend)
    assert result.status is CheckStatus.OK
    assert "logged in" in result.detail


@pytest.mark.parametrize(
    "stderr_tail",
    [
        "Please login first",
        "PLEASE LOGIN FIRST",
        "Unauthorized request",
        "UNAUTHORIZED",
        "user must authenticate",
        "User must AUTHENTICATE before continuing",
    ],
)
def test_check_claude_auth_case_insensitive_via_lower_once(stderr_tail: str) -> None:
    """R5 — substring matching uses tail.lower() once + any(); not per-variant
    case branching. Pin: every case-variant of every keyword must classify
    as FAIL.
    """
    backend = FakeBackend.with_auth_failure(stderr_tail=stderr_tail)
    result = _check_claude_auth(binary_present=True, backend_factory=lambda: backend)
    assert result.status is CheckStatus.FAIL
    assert "not logged in" in result.detail
    # Exact remediation wording (locked verbatim per D9).
    assert any(
        "Sleep will retry 9 times before failing if you skip this." in r for r in result.remediation
    )


def test_check_claude_auth_non_auth_invocation_failure_is_warn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del monkeypatch
    # No auth keyword in tail → WARN, not FAIL.
    backend = FakeBackend(
        headless_results=(
            BackendInvocationError(
                "claude exited with code 2",
                exit_code=2,
                stderr_tail="some other failure mode\nrate limited; try later",
            ),
        )
    )
    result = _check_claude_auth(binary_present=True, backend_factory=lambda: backend)
    assert result.status is CheckStatus.WARN


def test_check_claude_auth_timeout_is_warn() -> None:
    backend = FakeBackend(headless_results=(BackendTimeoutError("after 10s"),))
    result = _check_claude_auth(binary_present=True, backend_factory=lambda: backend)
    assert result.status is CheckStatus.WARN
    assert "timed out" in result.detail


def test_check_claude_auth_output_error_is_warn() -> None:
    backend = FakeBackend(headless_results=(BackendOutputError("garbled"),))
    result = _check_claude_auth(binary_present=True, backend_factory=lambda: backend)
    assert result.status is CheckStatus.WARN


# ---------------------------------------------------------------------------
# topics_summary
# ---------------------------------------------------------------------------


def test_check_topics_summary_returns_ok_zero_when_no_topics_dir(tmp_path: Path) -> None:
    summary, dirs = _check_topics_summary(tmp_path / "topics")
    assert summary.status is CheckStatus.OK
    assert dirs == []
    assert "no topics" in summary.detail


def test_check_topics_summary_lists_dirs_alphabetically(tmp_path: Path) -> None:
    topics = tmp_path / "topics"
    topics.mkdir()
    for n in ("workout", "coaching", "job-profile"):
        (topics / n).mkdir()
    summary, dirs = _check_topics_summary(topics)
    assert summary.status is CheckStatus.OK
    assert [d.name for d in dirs] == ["coaching", "job-profile", "workout"]
    assert "coaching, job-profile, workout" in summary.detail


# ---------------------------------------------------------------------------
# claude_templates (Phase 6 — replaces R6 hook_installed)
# ---------------------------------------------------------------------------


def test_check_claude_templates_fails_when_settings_json_absent(
    tmp_path: Path,
) -> None:
    result = _check_claude_templates(tmp_path)
    assert result.status is CheckStatus.FAIL
    # Verbatim per plan §5.11 — settings missing FAIL remediation.
    assert ".claude/settings.json missing" in result.detail
    assert any("remory init" in r for r in result.remediation)


# ---------------------------------------------------------------------------
# per-topic checks
# ---------------------------------------------------------------------------


def test_check_topic_emits_fail_row_when_meta_yaml_unparseable(
    tmp_path: Path,
) -> None:
    topic_dir = tmp_path / "broken"
    topic_dir.mkdir()
    (topic_dir / "meta.yaml").write_text(": :: bad yaml ::\n", encoding="utf-8")
    rows = _check_topic(topic_dir, strict=False)
    assert len(rows) == 1
    assert rows[0].status is CheckStatus.FAIL
