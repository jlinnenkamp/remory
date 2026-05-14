"""``remory doctor`` — health checks for data dir, config, claude, and topics.

The doctor is the user-facing recovery surface. It does not modify
anything; each ``_check_*`` returns a :class:`CheckResult` describing
what it found.

Execution order (consolidated plan §4.6, updated for Phase 6 §5.11):

1. ``data_dir``                — resolves and writes a probe file
2. ``config``                  — loads config.toml if present (R7: missing is OK)
3. ``claude_binary``           — ``shutil.which("claude")``
4. ``claude_auth``             — auth probe (R5 substring matching)
5. ``topics_summary``          — lists topic dirs
6-13. per topic: schema, state.md parse, state.md canonical (--strict),
       drift, lock orphan, tmp orphan, backups, pending orphan
14. ``claude_templates``       — bundled-template drift (Phase 6 §5.11)
15. ``per_topic_claude_md``    — per-topic CLAUDE.md drift (Phase 6 §5.11)
16. ``real_cli_probe``         — ``--probe-real-cli`` only

Auth-probe classification (R5):

    auth_keywords = ("login", "unauthorized", "authenticate")
    tail_lower = stderr_tail.lower()
    if any(k in tail_lower for k in auth_keywords):
        # FAIL — auth-likely
        ...

The ``.lower()`` once + ``any(k in tail_lower for k in keywords)`` form
is mandatory; not per-variant case branching. Pinned in the docstring of
:func:`_check_claude_auth` and asserted by
``test_check_claude_auth_case_insensitive_via_lower_once``.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import typer

from remory import config as cfgmod
from remory import paths, topic_claude_md
from remory.backends.base import (
    Backend,
    BackendInvocationError,
    BackendNotFoundError,
    BackendOutputError,
    BackendTimeoutError,
)
from remory.backends.claude_code import ClaudeCodeBackend
from remory.claude_assets import _detect_version_any  # pyright: ignore[reportPrivateUsage]
from remory.config import ConfigError
from remory.data_templates import iter_template_relpaths, read_template_bytes
from remory.locking import is_locked
from remory.raw import RawStatus, list_raw
from remory.schema import SchemaError
from remory.state import (
    StateParseError,
    is_canonical,
    read_state,
)
from remory.topic import TopicMetaError, load_topic
from remory.ui import CheckResult, CheckStatus, print_doctor_report

__all__ = ["run_doctor"]

_log = logging.getLogger("remory.commands.doctor")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_data_dir(data_dir: Path) -> CheckResult:
    """data_dir resolves and is writable."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".doctor.probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult(
            id="data_dir",
            status=CheckStatus.FAIL,
            label="data_dir",
            detail=f"not writable ({exc})",
            remediation=(f"check permissions on {data_dir} and re-run.",),
        )
    return CheckResult(
        id="data_dir",
        status=CheckStatus.OK,
        label="data_dir",
        detail=str(data_dir),
    )


def _check_config() -> CheckResult:
    """config.toml loads (R7: missing-file is OK, not INFO).

    Surfaces ConfigError (parse/validation failures) as FAIL with the
    file path; missing config.toml is OK with the "defaults" detail.
    """
    cfg_path = paths.config_dir() / "config.toml"
    if not cfg_path.exists():
        return CheckResult(
            id="config",
            status=CheckStatus.OK,
            label="config",
            detail="defaults (no config.toml found)",
        )
    try:
        cfgmod.load_config()
    except ConfigError as exc:
        return CheckResult(
            id="config",
            status=CheckStatus.FAIL,
            label="config",
            detail=f"{cfg_path}: {exc.validation_error}",
            remediation=(f"Edit {cfg_path} or remove it to fall back to defaults.",),
        )
    return CheckResult(
        id="config",
        status=CheckStatus.OK,
        label="config",
        detail=str(cfg_path),
    )


def _check_claude_binary() -> CheckResult:
    """``shutil.which('claude')``; version is hidden by default (D9)."""
    binary = shutil.which("claude")
    if binary is None:
        return CheckResult(
            id="claude_binary",
            status=CheckStatus.FAIL,
            label="claude binary",
            detail="not on PATH",
            remediation=("Install Claude Code, or check the binary is named 'claude'.",),
        )
    return CheckResult(
        id="claude_binary",
        status=CheckStatus.OK,
        label="claude binary",
        detail=binary,
    )


# R5 (locked): substring matching uses tail.lower() once + any().
_AUTH_KEYWORDS = ("login", "unauthorized", "authenticate")


def _check_claude_auth(
    *,
    binary_present: bool,
    backend_factory: Callable[[], Backend],
) -> CheckResult:
    """Run a single ``Backend.headless`` to probe authentication.

    Substring matching pattern (R5): one ``tail.lower()`` call, then
    ``any(k in tail_lower for k in keywords)``. Not per-variant case
    branching. Pinned by
    ``test_check_claude_auth_case_insensitive_via_lower_once``.

    Classification:

    * ``HeadlessResult`` → OK (logged in).
    * ``BackendNotFoundError`` → SKIP (binary check already failed).
    * ``BackendTimeoutError`` → WARN.
    * ``BackendInvocationError`` matching auth keywords → FAIL with §4.2
      remediation. Otherwise WARN with truncated stderr tail.
    * ``BackendOutputError`` → WARN.
    """
    if not binary_present:
        return CheckResult(
            id="claude_auth",
            status=CheckStatus.SKIP,
            label="claude auth",
            detail="(skipped — claude binary not on PATH)",
        )

    backend = backend_factory()
    try:
        result = backend.headless(prompt="ping", json_output=True, timeout_seconds=10)
    except BackendNotFoundError:
        return CheckResult(
            id="claude_auth",
            status=CheckStatus.SKIP,
            label="claude auth",
            detail="(skipped — claude binary not on PATH)",
        )
    except BackendTimeoutError:
        return CheckResult(
            id="claude_auth",
            status=CheckStatus.WARN,
            label="claude auth",
            detail="claude auth probe timed out after 10s. Check connectivity, then re-run.",
        )
    except BackendInvocationError as exc:
        tail = exc.stderr_tail or ""
        # R5: lower() ONCE, then `any(k in tail_lower for k in keywords)`.
        tail_lower = tail.lower()
        if any(k in tail_lower for k in _AUTH_KEYWORDS):
            return CheckResult(
                id="claude_auth",
                status=CheckStatus.FAIL,
                label="claude auth",
                detail="not logged in",
                remediation=(
                    "run `claude` once interactively to log in, then re-run "
                    "`remory doctor`. Sleep will retry 9 times before failing "
                    "if you skip this.",
                ),
            )
        # Non-auth invocation failure: WARN with truncated tail.
        head = (tail.strip().splitlines() or [""])[-1]
        return CheckResult(
            id="claude_auth",
            status=CheckStatus.WARN,
            label="claude auth",
            detail=f"claude exited non-zero during auth probe: {head[:200]}",
        )
    except BackendOutputError:
        return CheckResult(
            id="claude_auth",
            status=CheckStatus.WARN,
            label="claude auth",
            detail="claude returned malformed output during auth probe.",
        )

    # Success — surface a friendly "logged in as <id>" line. The real
    # claude doesn't expose the account from a `-p` JSON envelope; we
    # use the session_id (if any) as a best-effort hint.
    hint = result.session_id or "logged-in user"
    return CheckResult(
        id="claude_auth",
        status=CheckStatus.OK,
        label="claude auth",
        detail=f"logged in as {hint}",
    )


def _check_topics_summary(topics_root: Path) -> tuple[CheckResult, list[Path]]:
    """List topic dirs. Returns (summary_result, list_of_topic_dirs)."""
    if not topics_root.is_dir():
        return (
            CheckResult(
                id="topics",
                status=CheckStatus.OK,
                label="topics (0)",
                detail="no topics yet — try remory init",
            ),
            [],
        )
    topic_dirs = sorted([p for p in topics_root.iterdir() if p.is_dir()])
    if not topic_dirs:
        return (
            CheckResult(
                id="topics",
                status=CheckStatus.OK,
                label="topics (0)",
                detail="no topics yet — try remory init",
            ),
            [],
        )
    names = ", ".join(p.name for p in topic_dirs)
    return (
        CheckResult(
            id="topics",
            status=CheckStatus.OK,
            label=f"topics ({len(topic_dirs)})",
            detail=names,
        ),
        topic_dirs,
    )


def _check_topic(topic_dir: Path, *, strict: bool) -> list[CheckResult]:
    """Run the per-topic checks for one topic dir.

    Returns one or more rows. The first row carries the "topic: <name>"
    label so the doctor's renderer can group them.
    """
    name = topic_dir.name
    label = f"topic: {name}"

    # Schema/meta loadable.
    try:
        topic = load_topic(topic_dir)
    except (TopicMetaError, SchemaError) as exc:
        return [
            CheckResult(
                id=f"topic:{name}",
                status=CheckStatus.FAIL,
                label=label,
                detail=f"meta/schema load failed: {exc}",
                remediation=(f"inspect {topic_dir / 'meta.yaml'} by hand.",),
            ),
        ]

    rows: list[CheckResult] = []
    pending = topic.meta.pending_count
    detail = f"schema OK, {pending} pending {'entry' if pending == 1 else 'entries'}"
    rows.append(
        CheckResult(
            id=f"topic:{name}",
            status=CheckStatus.OK,
            label=label,
            detail=detail,
        ),
    )

    # state.md parseable / present.
    state_path = paths.state_file(topic_dir)
    if state_path.exists():
        try:
            doc = read_state(state_path)
        except StateParseError as exc:
            rows.append(
                CheckResult(
                    id=f"topic:{name}/state_parse",
                    status=CheckStatus.FAIL,
                    label=label,
                    detail=f"state.md parse failed: {exc}",
                    remediation=(f"restore from {paths.backups_dir(topic_dir)}.",),
                ),
            )
            doc = None
        else:
            # Drift sections: titles in state.md not in schema.
            schema_titles = {s.title for s in topic.schema.sections}
            drift = [s for s in doc.sections if s.title not in schema_titles]
            if drift:
                titles = ", ".join(repr(s.title) for s in drift)
                count = len(drift)
                noun = "section" if count == 1 else "sections"
                rows.append(
                    CheckResult(
                        id=f"topic:{name}/drift",
                        status=CheckStatus.WARN,
                        label=label,
                        detail=(
                            f"schema drift: {count} {noun} in state.md "
                            f"is not in the schema ({titles})."
                        ),
                        remediation=(
                            f"the next sleep will drop {'that' if count == 1 else 'those'} "
                            f"section{'' if count == 1 else 's'}. Move the content into a "
                            f"schema section, or add {titles} to the schema, before "
                            f"running `remory sleep {name}`.",
                        ),
                    ),
                )
            # Strict-only canonical-form check.
            if strict and not _is_canonical_safe(state_path):
                rows.append(
                    CheckResult(
                        id=f"topic:{name}/canonical",
                        status=CheckStatus.WARN,
                        label=label,
                        detail=(
                            "state.md is hand-edited; the next sleep will "
                            "canonicalise the YAML frontmatter (key order: "
                            "schema, schema_version, last_consolidated, "
                            "entries_consolidated; UTC datetimes rendered "
                            "with 'Z' suffix)."
                        ),
                        remediation=(
                            "diff after a sleep: cp state.md state.md.before; "
                            f"remory sleep {name}; diff state.md.before state.md.",
                        ),
                    ),
                )
    elif pending > 0:
        rows.append(
            CheckResult(
                id=f"topic:{name}/state_missing",
                status=CheckStatus.WARN,
                label=label,
                detail=(
                    f"state.md missing but {pending} pending "
                    f"{'entry' if pending == 1 else 'entries'} on disk."
                ),
                remediation=(f"`remory sleep {name}` will create state.md on first run.",),
            ),
        )

    # Lock orphan: .lock exists, not held, mtime > 1 hour.
    lock_path = topic_dir / ".lock"
    if lock_path.exists():
        try:
            mtime = lock_path.stat().st_mtime
        except OSError:
            mtime = time.time()
        age = time.time() - mtime
        if not is_locked(topic_dir) and age > 3600:
            rows.append(
                CheckResult(
                    id=f"topic:{name}/lock",
                    status=CheckStatus.FAIL,
                    label=label,
                    detail="stale .lock file (no holder)",
                    remediation=(f"remove {lock_path} and re-run `remory doctor`.",),
                ),
            )

    # Tmp orphans.
    tmps = list(topic_dir.glob("*.tmp"))
    if tmps:
        rows.append(
            CheckResult(
                id=f"topic:{name}/tmp_orphan",
                status=CheckStatus.WARN,
                label=label,
                detail=f"{len(tmps)} stale .tmp file(s) — sleep cleans these on next run.",
            ),
        )

    # Backups present (when state.md is populated).
    backups = paths.backups_dir(topic_dir)
    if state_path.exists() and not backups.is_dir():
        rows.append(
            CheckResult(
                id=f"topic:{name}/backups",
                status=CheckStatus.WARN,
                label=label,
                detail="state.md exists but no .backups directory yet (created on first sleep).",
            ),
        )

    # Pending orphan: pending raw with created < meta.last_consolidated.
    if topic.meta.last_consolidated is not None:
        pending_entries = list_raw(topic_dir, status=RawStatus.PENDING)
        orphans = [
            e for e in pending_entries if e.frontmatter.created < topic.meta.last_consolidated
        ]
        if orphans:
            rows.append(
                CheckResult(
                    id=f"topic:{name}/pending_orphan",
                    status=CheckStatus.WARN,
                    label=label,
                    detail=(f"{len(orphans)} pending raw entry(ies) older than last_consolidated."),
                    remediation=(f"`remory sleep {name}` will fold them in.",),
                ),
            )

    return rows


def _is_canonical_safe(state_path: Path) -> bool:
    """Wrapper that swallows StateParseError into ``False``.

    The canonical-check is downstream of the parse check; if parse
    already failed, the strict check would just duplicate that signal,
    so report the file as non-canonical and rely on the parse row to
    surface the real problem.
    """
    try:
        return is_canonical(state_path)
    except StateParseError:
        return False


def _check_claude_templates(data_dir: Path) -> CheckResult:
    """Phase 6 (plan §5.11): bundled-template drift check.

    Classifies the data-dir ``.claude/`` tree against the bundled
    templates. Per plan §5.11:

    - settings.json missing → FAIL with "remory init" remediation.
    - settings.json malformed JSON → FAIL with "remory init --refresh
      --force" remediation.
    - All bundled files byte-match disk → OK.
    - Some bundled files stamped-older on disk → WARN.
    - Some bundled files stamped-current but byte-edited → WARN.

    Replaces the v0.1 placeholder ``_check_hook_installed`` (R6); the
    settings-missing FAIL branch is the new owner of the
    "hook not installed" signal.
    """
    settings_path = data_dir / ".claude" / "settings.json"
    if not settings_path.exists():
        return CheckResult(
            id="claude_templates",
            status=CheckStatus.FAIL,
            label="claude templates",
            detail=".claude/settings.json missing",
            remediation=("run `remory init` to recreate",),
        )

    # settings.json must parse as JSON; malformed bytes are a FAIL with
    # the --refresh --force remediation (which writes a .bak first).
    try:
        json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return CheckResult(
            id="claude_templates",
            status=CheckStatus.FAIL,
            label="claude templates",
            detail=f".claude/settings.json malformed: {first_line}",
            remediation=("run `remory init --refresh --force` to recreate (.bak saved)",),
        )

    total = 0
    stale_older: list[str] = []
    edited: list[str] = []
    for relpath in iter_template_relpaths():
        total += 1
        bundled = read_template_bytes(relpath)
        on_disk_path = data_dir / relpath
        if not on_disk_path.exists():
            # Treat missing-but-not-settings as a stale-older signal
            # (the user can run --refresh to repair). This is a
            # softer surface than FAIL because the user already gets a
            # FAIL row when settings.json is missing.
            stale_older.append(relpath)
            continue
        try:
            disk_bytes = on_disk_path.read_bytes()
        except OSError:
            stale_older.append(relpath)
            continue
        if disk_bytes == bundled:
            continue
        bundled_version = _detect_version_any(relpath, bundled)
        disk_version = _detect_version_any(relpath, disk_bytes)
        if (
            disk_version is not None
            and bundled_version is not None
            and disk_version < bundled_version
        ):
            stale_older.append(relpath)
        else:
            edited.append(relpath)

    if not stale_older and not edited:
        return CheckResult(
            id="claude_templates",
            status=CheckStatus.OK,
            label="claude templates",
            detail=f"current ({total} file(s) match bundle)",
        )

    if edited:
        listed = ", ".join(_strip_claude_prefix(p) for p in edited)
        count = len(edited)
        return CheckResult(
            id="claude_templates",
            status=CheckStatus.WARN,
            label="claude templates",
            detail=f"{count} file(s) edited after stamping ({listed})",
            remediation=(
                "run `remory init --refresh --dry-run` to inspect; "
                "`--refresh --force` to overwrite (.bak saved)",
            ),
        )

    # Stale-older only.
    return CheckResult(
        id="claude_templates",
        status=CheckStatus.WARN,
        label="claude templates",
        detail=f"{len(stale_older)} of {total} file(s) stale (older template version)",
        remediation=("run `remory init --refresh --dry-run` to inspect",),
    )


def _strip_claude_prefix(relpath: str) -> str:
    """Strip a leading ``.claude/`` from a bundled relpath for display."""
    return relpath[len(".claude/") :] if relpath.startswith(".claude/") else relpath


def _check_per_topic_claude_md(data_dir: Path) -> CheckResult:
    """Phase 6 (plan §5.11): per-topic ``CLAUDE.md`` drift check.

    Re-renders each topic's ``CLAUDE.md`` via
    :func:`remory.topic_claude_md.render` and compares bytes to disk.
    Single summary line:

    - all topics byte-match → OK ("current for all N topic(s)").
    - any topic stale → WARN ("M of N topic(s) stale (names)").
    - no topics yet → OK ("no topics yet" — the cheapest user signal).
    """
    topics_root = data_dir / "topics"
    if not topics_root.is_dir():
        return CheckResult(
            id="per_topic_claude_md",
            status=CheckStatus.OK,
            label="per-topic CLAUDE.md",
            detail="no topics yet",
        )
    topic_dirs = sorted([p for p in topics_root.iterdir() if p.is_dir()])
    if not topic_dirs:
        return CheckResult(
            id="per_topic_claude_md",
            status=CheckStatus.OK,
            label="per-topic CLAUDE.md",
            detail="no topics yet",
        )

    stale: list[str] = []
    total = 0
    for topic_dir in topic_dirs:
        meta_path = paths.meta_file(topic_dir)
        if not meta_path.is_file():
            continue  # not a real topic
        total += 1
        try:
            topic = load_topic(topic_dir)
        except (TopicMetaError, SchemaError):
            # Doctor's per-topic check already reports the FAIL on the
            # parse row; don't double-count here.
            continue
        ctx = topic_claude_md.TopicClaudeMdContext(
            schema_name=topic.schema.name,
            persona=topic.schema.persona,
            tone=topic.meta.knobs.tone,
            strictness=topic.meta.knobs.strictness,
        )
        rendered = topic_claude_md.render(ctx).encode("utf-8")
        target = paths.claude_md_file(topic_dir)
        if not target.exists() or target.read_bytes() != rendered:
            stale.append(topic_dir.name)

    if total == 0:
        return CheckResult(
            id="per_topic_claude_md",
            status=CheckStatus.OK,
            label="per-topic CLAUDE.md",
            detail="no topics yet",
        )
    if not stale:
        return CheckResult(
            id="per_topic_claude_md",
            status=CheckStatus.OK,
            label="per-topic CLAUDE.md",
            detail=f"current for all {total} topic(s)",
        )
    names = ", ".join(stale)
    return CheckResult(
        id="per_topic_claude_md",
        status=CheckStatus.WARN,
        label="per-topic CLAUDE.md",
        detail=f"{len(stale)} of {total} topic(s) stale ({names})",
        remediation=("run `remory init --refresh --dry-run` to inspect",),
    )


def _check_real_cli_probe(*, backend_factory: Callable[[], Backend]) -> CheckResult:
    """``--probe-real-cli`` round-trip path-encoding probe (CC10 opt-in).

    Asks the backend to chat (or headless) once and confirms the
    transcript landed where ``transcripts.encode_cwd_for_claude``
    expected. Off by default; one extra LLM call when enabled.
    """
    import tempfile

    from remory import transcripts

    backend = backend_factory()
    # Capture claude version for FAIL diagnostics; a None means health_check
    # didn't surface it (e.g. binary missing — which is a separate check).
    health = backend.health_check()
    version_suffix = f" (claude {health.version})" if health.version is not None else ""
    with tempfile.TemporaryDirectory(prefix="remory-doctor-probe-") as td:
        cwd = Path(td)
        try:
            backend.headless(
                prompt="ping",
                cwd=cwd,
                json_output=True,
                timeout_seconds=20,
            )
        except (BackendNotFoundError, BackendInvocationError, BackendTimeoutError) as exc:
            return CheckResult(
                id="real_cli_probe",
                status=CheckStatus.FAIL,
                label="real-cli probe",
                detail=f"probe could not run: {exc}{version_suffix}",
                remediation=(
                    f"file an issue with this output and your claude version{version_suffix}.",
                ),
            )
        encoded = transcripts.encode_cwd_for_claude(cwd.resolve())
        located = transcripts.locate_latest(cwd)
        if located is None:
            return CheckResult(
                id="real_cli_probe",
                status=CheckStatus.FAIL,
                label="real-cli probe",
                detail=(
                    f"our cwd-encoder produced {encoded!r} but claude wrote "
                    f"no transcript at the expected location{version_suffix}."
                ),
                remediation=(
                    f"file an issue with this output and your claude version{version_suffix}.",
                ),
            )
    return CheckResult(
        id="real_cli_probe",
        status=CheckStatus.OK,
        label="real-cli probe",
        detail=f"path-encoded transcript matches our locator (encoded as {encoded})",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _default_backend_factory() -> Backend:
    return ClaudeCodeBackend()


def run_doctor(
    *,
    strict: bool = False,
    probe_real_cli: bool = False,
    backend_factory: Callable[[], Backend] | None = None,
) -> None:
    """Run all doctor checks and print the report.

    Exit code policy: 0 on clean run; 1 when any FAIL row is present.
    The CLI surface translates the return into ``typer.Exit``.
    """
    factory = backend_factory if backend_factory is not None else _default_backend_factory

    cfg: cfgmod.Config | None
    try:
        cfg = cfgmod.load_config()
    except ConfigError:
        cfg = None
    data_dir = paths.data_dir() if cfg is None else cfgmod.resolve_data_dir(cfg)

    results: list[CheckResult] = []
    results.append(_check_data_dir(data_dir))
    results.append(_check_config())
    binary_row = _check_claude_binary()
    results.append(binary_row)
    binary_present = binary_row.status is CheckStatus.OK
    results.append(_check_claude_auth(binary_present=binary_present, backend_factory=factory))

    summary, topic_dirs = _check_topics_summary(data_dir / "topics")
    results.append(summary)

    for topic_dir in topic_dirs:
        results.extend(_check_topic(topic_dir, strict=strict))

    results.append(_check_claude_templates(data_dir))
    results.append(_check_per_topic_claude_md(data_dir))

    if probe_real_cli:
        results.append(_check_real_cli_probe(backend_factory=factory))

    print_doctor_report(results, cfg=cfg)

    has_fail = any(r.status is CheckStatus.FAIL for r in results)
    if has_fail:
        raise typer.Exit(code=1)
