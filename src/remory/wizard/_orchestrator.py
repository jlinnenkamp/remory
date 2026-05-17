"""Wizard orchestrator: claude-driven interview (Phase 6 rearchitecture).

The Python harness owns three responsibilities:

1. **Preflight** — confirm the ``claude`` binary is on PATH and the user
   is authenticated. Reuses the doctor's probes; on failure, refuses to
   launch and points the user at ``remory doctor``. No offline fallback.
2. **Run-dir staging + subagent launch** — stage built-in schemas into a
   tempdir, then invoke ``claude --agent wizard`` with ``cwd`` set to
   the **data directory root** (load-bearing: D4 / ADR-0002).
3. **Parse, repair, commit** — read ``answers.json`` + ``letter.md``;
   on parse failure, one repair round with the error embedded; on
   second failure, dump a recovery dir and abort. On success, hand off
   to :func:`remory.wizard._commit.commit`.

The wizard's voice is the model's voice; the harness has no Python
prompting and no fallback paragraph. See ADR-0006.
"""

from __future__ import annotations

import logging
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from remory import config as cfgmod
from remory import paths
from remory.backends.base import Backend
from remory.backends.claude_code import ClaudeCodeBackend
from remory.claude_assets import install_data_dir_templates

# Wizard preflight reuses the doctor's probes by design (plan §7): the
# wizard and doctor must classify the same way (no drift). The leading-
# underscore names are internal to doctor_cmd; we accept the private-usage
# pyright warning here because reimplementing the probes would risk drift.
from remory.commands.doctor_cmd import (
    _check_claude_auth,  # pyright: ignore[reportPrivateUsage]
    _check_claude_binary,  # pyright: ignore[reportPrivateUsage]
)
from remory.ui import CheckStatus, make_console
from remory.wizard import _strings as S
from remory.wizard._answers import WizardAnswers
from remory.wizard._commit import commit
from remory.wizard._subagent import (
    REPAIR_PROMPT_FILE_NAME,
    SubagentRunResult,
    WizardAnswerParseError,
    dump_recovery,
    parse_run_dir,
    stage_run_dir,
)

__all__ = [
    "WizardAnswerParseError",
    "WizardPreflightError",
    "WizardSubagentFailedError",
    "run_wizard",
]

_log = logging.getLogger("remory.wizard.orchestrator")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WizardPreflightError(Exception):
    """Preflight refused to launch the wizard.

    Carries a short ``reason`` (e.g. ``"binary not on PATH"`` or
    ``"auth probe failed"``) for logging. The user-facing message is
    :data:`remory.wizard._strings.PRECONDITION_NEEDS_DOCTOR_MESSAGE`,
    rendered by ``cli/errors.py``.
    """


class WizardSubagentFailedError(Exception):
    """The wizard subagent exited non-zero or produced unparseable output twice.

    Carries an optional :attr:`recovery_dir` for the second-strike case
    (validation failed twice; recovery written). When unset, the
    subagent exited non-zero before writing anything parseable.
    """

    def __init__(self, message: str, *, recovery_dir: Path | None = None) -> None:
        super().__init__(message)
        self.recovery_dir = recovery_dir


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PreflightResult:
    ok: bool
    reason: str


def _preflight_claude_or_doctor(backend: Backend) -> _PreflightResult:
    """Probe the claude binary AND auth. Both must be OK to proceed.

    Reuses :func:`remory.commands.doctor_cmd._check_claude_binary` and
    :func:`remory.commands.doctor_cmd._check_claude_auth` so the wizard
    and the doctor classify the same way (no drift). The auth check
    issues a single ``Backend.headless`` round-trip; failures are
    classified per the doctor's keyword table (login / unauthorized /
    authenticate).
    """
    binary_row = _check_claude_binary()
    if binary_row.status is not CheckStatus.OK:
        return _PreflightResult(ok=False, reason="claude binary not on PATH")

    auth_row = _check_claude_auth(
        binary_present=True,
        backend_factory=lambda: backend,
    )
    if auth_row.status is not CheckStatus.OK:
        # WARN, FAIL, or SKIP — none is good enough to launch. The
        # doctor's classifier already produced a per-row reason; we
        # pull a short reason for logs without leaking it to the user.
        return _PreflightResult(ok=False, reason=f"claude auth probe: {auth_row.status.value}")

    return _PreflightResult(ok=True, reason="ok")


# ---------------------------------------------------------------------------
# Repair-prompt staging
# ---------------------------------------------------------------------------


def _stage_repair_prompt(run_dir: Path, error_message: str) -> Path:
    """Write a small repair-prompt file the subagent can read on retry.

    Contract: the orchestrator writes ``<run_dir>/repair_prompt.txt``
    containing a human-readable description of what failed. The repair
    initial_prompt (see :func:`_build_repair_prompt`) tells the
    subagent to Read this file, so the validation error is surfaced
    regardless of whether ``--resume`` preserves prior context.
    """
    target = run_dir / REPAIR_PROMPT_FILE_NAME
    body = (
        "Your previous attempt produced invalid output. The harness saw:\n\n"
        f"{error_message}\n\n"
        "Please re-write `answers.json` and `letter.md` per the wizard.md schema, "
        "then stop.\n"
    )
    target.write_text(body, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Run-directory layout
# ---------------------------------------------------------------------------

# Fixed path inside the data dir so the wizard.md template can hard-code
# the location (relative to cwd, which the launcher sets to data_dir).
# Two benefits over a /tmp tempdir: (1) it's inside cwd, so claude trusts
# it without an "outside the project" permission prompt; (2) the path is
# stable, so the wizard subagent's instructions don't have to be
# parameterised per launch. Wiped at the start of each run; left in
# place after the run so a failed-but-not-recovery-dumped session can
# still be inspected before the next `remory init` cycle.
WIZARD_RUN_DIR_RELATIVE: str = ".remory/wizard-run-current"


def _wizard_run_dir(data_dir: Path) -> Path:
    return data_dir / WIZARD_RUN_DIR_RELATIVE


# ---------------------------------------------------------------------------
# Initial-prompt construction
# ---------------------------------------------------------------------------

# Kept minimal on purpose. claude's interactive `--agent X` mode requires
# a user-side first turn before the agent speaks; we send the shortest
# natural opener that doesn't read as the user instructing the model.
# The wizard.md template carries the run-dir path (now stable) and the
# "speak first when the user opens with a kick-off" behaviour, so this
# message stays plumbing-free from the user's perspective.
_INITIAL_PROMPT: str = "Help me get started."


def _build_repair_prompt() -> str:
    """Compose the repair-round first-turn prompt.

    The harness has already written ``repair_prompt.txt`` into the
    fixed run directory; the prompt below tells the subagent to Read
    it before re-writing ``answers.json`` + ``letter.md``. Sent
    alongside ``resume=True`` so the prior conversation is still in
    context, but self-sufficient if claude drops the subagent across
    the resume.
    """
    return (
        f"Something went wrong with the last answer file. The validation error "
        f"is at {WIZARD_RUN_DIR_RELATIVE}/{REPAIR_PROMPT_FILE_NAME} — please read "
        f"it, then re-write {WIZARD_RUN_DIR_RELATIVE}/answers.json and "
        f"{WIZARD_RUN_DIR_RELATIVE}/letter.md to match what we just talked about."
    )


# ---------------------------------------------------------------------------
# Outro
# ---------------------------------------------------------------------------


def _print_outro(
    console: Console,
    data_dir: Path,
    answers: WizardAnswers,
) -> None:
    """Render the §5.9 outro template (singular vs plural) on ``console``."""
    chosen = list(answers.chosen_topics)
    about_me_path_str = str(paths.about_me_file(data_dir))
    data_dir_str = str(data_dir)
    if len(chosen) == 1:
        console.out(
            S.OUTRO_SINGULAR_TEMPLATE.format(
                data_dir=data_dir_str,
                topic=chosen[0],
                about_me_path=about_me_path_str,
            )
        )
        return
    if len(chosen) == 0:
        # Defensive: zero-topic wizard runs still produce an about-me.md;
        # render the singular template with a sentinel value so the user
        # gets feedback. This shouldn't happen in practice (the subagent
        # is asked to skip to step 5 in that case), but we don't crash.
        console.out(
            S.OUTRO_SINGULAR_TEMPLATE.format(
                data_dir=data_dir_str,
                topic="(none)",
                about_me_path=about_me_path_str,
            )
        )
        return
    second = chosen[1]
    console.out(
        S.OUTRO_PLURAL_TEMPLATE.format(
            data_dir=data_dir_str,
            topics_csv=", ".join(chosen),
            about_me_path=about_me_path_str,
            first_topic=chosen[0],
            second_topic=second,
        )
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _default_backend_factory() -> Backend:
    return ClaudeCodeBackend()


def _resolve_data_dir() -> Path:
    """Resolve the effective data directory at wizard entry.

    Mirrors :func:`remory.cli._resolve_data_dir_or_exit` but kept local
    to avoid pulling the CLI module into the wizard's import graph.
    """
    try:
        cfg = cfgmod.load_config()
    except cfgmod.ConfigError:
        return paths.data_dir()
    return cfgmod.resolve_data_dir(cfg)


def run_wizard(
    *,
    backend_factory: Callable[[], Backend] | None = None,
    console: Console | None = None,
    data_dir: Path | None = None,
) -> None:
    """Drive the claude-driven wizard flow end-to-end.

    Steps:

    1. Preflight — :func:`_preflight_claude_or_doctor`. On failure,
       writes :data:`PRECONDITION_NEEDS_DOCTOR_MESSAGE` to stderr and
       raises :class:`WizardPreflightError`.
    2. Install bundled ``.claude/`` templates idempotently
       (``force=False`` — preserves any user edits).
    3. Stage a tempdir run directory with schemas + manifest.
    4. ``backend.chat(cwd=data_dir, agent="wizard", resume=False)``.
       The ``cwd=data_dir`` is **load-bearing** (D4 / ADR-0002): the
       SessionEnd hook's "not under topics/<name>/" branch is the
       wizard-transcript skip mechanism.
    5. Parse ``answers.json`` + ``letter.md``. On parse failure, one
       repair round with the validation error embedded
       (``backend.chat(..., resume=True)``). A second failure dumps a
       recovery directory and raises.
    6. :func:`remory.wizard._commit.commit` writes the topic dirs +
       ``about-me.md`` under per-topic-atomic + deferred-SIGINT.
    7. Render the §5.9 outro on ``console``.

    KeyboardInterrupt before COMMIT propagates unchanged after stderr
    writes the locked pre-commit message; mid-COMMIT KeyboardInterrupt
    converts to :class:`WizardSigintDuringCommitError` inside
    :func:`commit`.
    """
    eff_data_dir = data_dir if data_dir is not None else _resolve_data_dir()
    eff_backend = (backend_factory or _default_backend_factory)()
    eff_console = console if console is not None else make_console()

    # --- 1. Preflight --------------------------------------------------------
    preflight = _preflight_claude_or_doctor(eff_backend)
    if not preflight.ok:
        sys.stderr.write(S.PRECONDITION_NEEDS_DOCTOR_MESSAGE)
        raise WizardPreflightError(preflight.reason)

    # --- 2. Install .claude/ templates (idempotent) -------------------------
    eff_data_dir.mkdir(parents=True, exist_ok=True)
    install_data_dir_templates(eff_data_dir, force=False)

    # --- 3-5. Stage run dir, launch subagent, parse + one repair round -----
    run_dir = _wizard_run_dir(eff_data_dir)
    # Wipe any leftover from a prior aborted run; the operator's only
    # interest in old wizard-run-current contents is between runs, and
    # the recovery dump already captures the salient bits on failure.
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    stage_run_dir(run_dir)

    # First attempt.
    try:
        first_result = eff_backend.chat(
            cwd=eff_data_dir,
            agent="wizard",
            resume=False,
            initial_prompt=_INITIAL_PROMPT,
        )
    except KeyboardInterrupt:
        sys.stderr.write(S.PRE_COMMIT_INTERRUPT_MESSAGE)
        raise

    if first_result.exit_code != 0:
        sys.stderr.write(S.PRE_COMMIT_INTERRUPT_MESSAGE)
        raise WizardSubagentFailedError(
            f"wizard subagent exited with code {first_result.exit_code}"
        )

    try:
        run_result: SubagentRunResult = parse_run_dir(run_dir)
    except WizardAnswerParseError as exc1:
        _log.warning(
            "wizard subagent produced unparseable output; starting repair round",
            extra={"exception_type": type(exc1).__name__, "wizard_step": "parse"},
        )
        _stage_repair_prompt(run_dir, exc1.message)
        try:
            second_result = eff_backend.chat(
                cwd=eff_data_dir,
                agent="wizard",
                resume=True,
                initial_prompt=_build_repair_prompt(),
            )
        except KeyboardInterrupt:
            # Mid-repair Ctrl+C still means nothing committed; dump
            # what we have so far so the user's prior turns aren't
            # lost (memory feedback_no_silent_data_loss).
            recovery_dir = dump_recovery(eff_data_dir, run_dir, exc1)
            sys.stderr.write(S.RECOVERY_MESSAGE_TEMPLATE.format(recovery_dir=recovery_dir))
            raise

        if second_result.exit_code != 0:
            recovery_dir = dump_recovery(eff_data_dir, run_dir, exc1)
            sys.stderr.write(S.RECOVERY_MESSAGE_TEMPLATE.format(recovery_dir=recovery_dir))
            raise WizardSubagentFailedError(
                f"wizard subagent (repair) exited with code {second_result.exit_code}",
                recovery_dir=recovery_dir,
            ) from exc1

        try:
            run_result = parse_run_dir(run_dir)
        except WizardAnswerParseError as exc2:
            recovery_dir = dump_recovery(eff_data_dir, run_dir, exc2)
            sys.stderr.write(S.RECOVERY_MESSAGE_TEMPLATE.format(recovery_dir=recovery_dir))
            raise WizardSubagentFailedError(
                "wizard subagent produced unparseable output twice",
                recovery_dir=recovery_dir,
            ) from exc2

    # --- 6. COMMIT -----------------------------------------------------------
    commit(run_result.answers, run_result.letter, data_dir=eff_data_dir)

    # --- 7. Outro ------------------------------------------------------------
    _print_outro(eff_console, eff_data_dir, run_result.answers)
