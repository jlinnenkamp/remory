"""Integration tests for ``remory doctor`` end-to-end."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import typer

from remory.commands.doctor_cmd import run_doctor
from remory.commands.init_cmd import run_init
from tests.fakes.fake_backend import FakeBackend

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fixtures")


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REMORY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("REMORY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMORY_CONFIG_FILE", raising=False)
    yield tmp_path


def test_run_doctor_clean_run_does_not_exit_when_no_failures(
    isolated_xdg: Path,
    fake_claude_on_path: tuple[Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Clean run: data_dir writable, no config.toml, claude binary
    discoverable via fake_claude_on_path, auth probe successful via a
    FakeBackend factory; no topics; should NOT exit non-zero.
    """
    del isolated_xdg, fake_claude_on_path

    from remory.backends.base import HeadlessMeta, HeadlessResult

    ok = HeadlessResult(
        text="pong",
        session_id="sess-ok",
        duration_ms=1,
        num_turns=1,
        stop_reason="end_turn",
        meta=HeadlessMeta(),
    )
    backend = FakeBackend(headless_results=(ok,))
    run_doctor(strict=False, probe_real_cli=False, backend_factory=lambda: backend)
    out = capsys.readouterr().out
    assert "checks" in out
    # Clean run footer.
    assert "0 failures" in out


def test_run_doctor_fails_with_exit_1_when_topic_meta_unparseable(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    # Corrupt meta.yaml.
    (topic_dir / "meta.yaml").write_text("garbage: : :", encoding="utf-8")
    # Hide claude from PATH so auth probe is SKIPped; the test focuses
    # on the topic FAIL row, not auth classification.
    monkeypatch.setenv("PATH", str(isolated_xdg))
    backend = FakeBackend()
    with pytest.raises(typer.Exit) as ei:
        run_doctor(strict=False, probe_real_cli=False, backend_factory=lambda: backend)
    assert ei.value.exit_code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out or "fail" in out


def test_run_doctor_strict_warns_on_handedited_state_md(
    isolated_xdg: Path,
    fake_claude_on_path: tuple[Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    del fake_claude_on_path
    run_init(topic_name="workout", schema_name="workout")
    topic_dir = isolated_xdg / "data" / "topics" / "workout"
    state_path = topic_dir / "state.md"
    # Hand-edit: scramble key order so render_state would re-format it.
    text = state_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    swapped: list[str] = []
    found_schema = False
    for i, line in enumerate(lines):
        if line.startswith("schema:") and not found_schema:
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("schema_version:"):
                    sv = lines[j]
                    swapped.append(sv)
                    swapped.append(line)
                    lines[j] = ""
                    found_schema = True
                    break
            else:
                swapped.append(line)
        elif line:
            swapped.append(line)
    state_path.write_text("\n".join(swapped) + "\n", encoding="utf-8")

    from remory.backends.base import HeadlessMeta, HeadlessResult

    ok = HeadlessResult(
        text="pong",
        session_id="sess-ok",
        duration_ms=1,
        num_turns=1,
        stop_reason="end_turn",
        meta=HeadlessMeta(),
    )
    backend = FakeBackend(headless_results=(ok,))
    # Doctor should NOT fail (no FAIL), only WARN.
    run_doctor(strict=True, probe_real_cli=False, backend_factory=lambda: backend)
    out = capsys.readouterr().out
    assert "warn" in out
    assert "state.md is hand-edited" in out


def test_run_doctor_clean_run_emits_r7_defaults_no_config_toml_found_line(
    isolated_xdg: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """R7 — missing config.toml renders 'defaults (no config.toml found)' OK row."""
    import contextlib

    del isolated_xdg
    monkeypatch.setenv("PATH", "/nonexistent")
    backend = FakeBackend()
    with contextlib.suppress(SystemExit, typer.Exit):
        run_doctor(strict=False, probe_real_cli=False, backend_factory=lambda: backend)
    out = capsys.readouterr().out
    assert "defaults (no config.toml found)" in out


def test_run_doctor_classifies_login_stderr_as_fail_with_locked_remediation(
    isolated_xdg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R5 + D9 — auth-keyword classifier produces the locked verbatim
    'Sleep will retry 9 times before failing if you skip this.' line.
    """
    del isolated_xdg
    backend = FakeBackend.with_auth_failure(stderr_tail="please login to continue")
    with pytest.raises(typer.Exit):
        run_doctor(strict=False, probe_real_cli=False, backend_factory=lambda: backend)
    out = capsys.readouterr().out
    assert "Sleep will retry 9 times before failing if you skip this." in out
