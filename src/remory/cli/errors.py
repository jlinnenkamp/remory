"""Error → user-message + exit-code mapping for the CLI surface.

Every command's top-level handler routes through :func:`format_error`.
The mapping table is binding (consolidated plan §6).

Exit codes (CC5):

* 0  success
* 1  generic runtime
* 2  usage
* 3  backend-not-found
* 4  backend-auth
* 5  backend-other
* 6  lock-busy
* 7  sleep-pipeline
* 8  data-parse
* 9  config
* 99 uncaught
* 130 SIGINT
"""

from __future__ import annotations

import logging
from pathlib import Path

from remory import paths
from remory.backends.base import (
    BackendAuthError,
    BackendInvocationError,
    BackendNotFoundError,
    BackendOutputError,
    BackendTimeoutError,
)
from remory.config import ConfigError
from remory.locking import LockBusyError
from remory.raw import RawWriteError
from remory.schema import SchemaError
from remory.sleep.critique import CritiqueError
from remory.sleep.extract import ExtractError
from remory.sleep.merge import MergeError
from remory.sleep.orchestrator import SleepError
from remory.state import StateParseError
from remory.topic import TopicMetaError
from remory.wizard import (
    WIZARD_REDIRECT_MESSAGE,
    WizardAboutMeError,
    WizardCommitPartialError,
    WizardPreflightError,
    WizardRedirectError,
    WizardSigintDuringCommitError,
    WizardSubagentFailedError,
)
from remory.wizard import _strings as _wizard_strings

__all__ = [
    "TopicExistsError",
    "TopicIncompleteError",
    "TopicMissingError",
    "format_error",
]


_log = logging.getLogger("remory.cli.errors")


# ---------------------------------------------------------------------------
# CLI-side exception adapters (D6 + D7)
# ---------------------------------------------------------------------------


class TopicMissingError(Exception):
    """Topic directory does not exist (D6 first arm)."""

    def __init__(self, name: str, *, existing_topics: tuple[str, ...]) -> None:
        super().__init__(f"topic {name!r} does not exist")
        self.name = name
        self.existing_topics = existing_topics


class TopicIncompleteError(Exception):
    """Topic directory exists but state.md or meta.yaml missing/unparseable (D6 second arm)."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"topic {name!r} is incomplete: {reason}")
        self.name = name
        self.reason = reason


class TopicExistsError(Exception):
    """`remory init` refused to overwrite an existing topic (D7)."""

    def __init__(self, name: str, topic_dir: Path) -> None:
        super().__init__(f"topic {name!r} already exists at {topic_dir}")
        self.name = name
        self.topic_dir = topic_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _logs_path(data_dir: Path) -> Path:
    """Best-effort path to the user-facing log file.

    The data_dir parameter is unused for the logs path itself (logs live
    under the state dir, not the data dir) but is part of the signature
    so callers consistently pass it for future use.
    """
    del data_dir
    return paths.logs_dir() / "remory.log"


def _truncate_stderr(tail: str | None, *, max_lines: int = 6) -> str:
    if not tail:
        return ""
    lines = tail.strip().splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


# ---------------------------------------------------------------------------
# format_error
# ---------------------------------------------------------------------------


def format_error(exc: BaseException, *, data_dir: Path) -> tuple[str, int]:
    """Map ``exc`` to (user_message, exit_code) per the §6 table.

    Returns the message **with** trailing newline; the caller writes
    directly to stderr.
    """
    logs = _logs_path(data_dir)

    # Wizard redirect (R3 wording; alias WizardNotBuiltError preserved) ---
    # Note: WizardNotBuiltError is an alias for WizardRedirectError per
    # R3, so a single isinstance check covers both names. The user-visible
    # text is always WIZARD_REDIRECT_MESSAGE; we ignore exc.args so a
    # caller passing a stray string can't leak it to the user.
    if isinstance(exc, WizardRedirectError):
        return f"{WIZARD_REDIRECT_MESSAGE}\n", 2

    # Wizard preflight failure (Phase 6 D2) ------------------------------
    # Either the claude binary is missing or auth probe failed. The
    # message points at `remory doctor` so the user can fix the
    # precondition; exit 2 is the same code other usage errors use.
    if isinstance(exc, WizardPreflightError):
        return _wizard_strings.PRECONDITION_NEEDS_DOCTOR_MESSAGE, 2

    # Wizard subagent failure (Phase 6 D2) -------------------------------
    # The subagent exited non-zero or produced unparseable output twice.
    # When a recovery dir is present, mention it; otherwise fall back to
    # the locked pre-commit interrupt message (no files written).
    if isinstance(exc, WizardSubagentFailedError):
        recovery_dir = exc.recovery_dir
        if recovery_dir is not None:
            return (
                _wizard_strings.RECOVERY_MESSAGE_TEMPLATE.format(recovery_dir=recovery_dir),
                1,
            )
        return _wizard_strings.PRE_COMMIT_INTERRUPT_MESSAGE, 1

    # Wizard SIGINT during COMMIT (consolidated plan §3.8) ----------------
    # In-flight write completes; subsequent files were not written. The
    # message is the locked "Stopped mid-write…" wording, exit 130.
    if isinstance(exc, WizardSigintDuringCommitError):
        return (
            "Stopped mid-write. Some files may exist. Run remory doctor to inspect.\n",
            130,
        )

    # Wizard COMMIT partial-failure (ADR 0003) ----------------------------
    if isinstance(exc, WizardCommitPartialError):
        if exc.prior_topic is not None:
            msg = (
                f"Stopped mid-write at topic '{exc.failed_topic}'. Topic "
                f"'{exc.prior_topic}' was created\nsuccessfully. Run remory "
                f"doctor to inspect, or remory init {exc.failed_topic} to\n"
                "retry the failed topic.\n"
            )
        else:
            msg = (
                f"Stopped mid-write at topic '{exc.failed_topic}'. Run "
                f"remory doctor to inspect, or\nremory init {exc.failed_topic} "
                "to retry the failed topic.\n"
            )
        return msg, 1

    # Wizard about-me failure (consolidated plan §8) ----------------------
    if isinstance(exc, WizardAboutMeError):
        return (
            "All topics created, but about-me.md couldn't be written. Run remory doctor.\n",
            1,
        )

    # Path validation (init) — ValueError from _validate_topic_name -------
    if isinstance(exc, ValueError) and "topic name" in str(exc):
        return f"{exc!s}\n", 2

    # Topic-state errors (D6 + D7) ----------------------------------------
    if isinstance(exc, TopicMissingError):
        if exc.existing_topics:
            existing = ", ".join(exc.existing_topics)
            remediation = f"Run remory init {exc.name} to set it up. Existing topics: {existing}"
        else:
            remediation = "Run remory init to set one up."
        msg = f"Topic {exc.name!r} doesn't exist yet.\n{remediation}\n"
        return msg, 2

    if isinstance(exc, TopicIncompleteError):
        msg = (
            f"Topic {exc.name!r} is in an incomplete state.\n"
            "Run remory doctor to inspect — init could overwrite partial files.\n"
        )
        return msg, 2

    if isinstance(exc, TopicExistsError):
        # D7 — pinned 3-line wording, verbatim.
        msg = (
            f"Topic '{exc.name}' already exists at {exc.topic_dir}. To re-run the "
            "wizard for it,\n"
            f"delete the topic directory first (`rm -rf {exc.topic_dir}`) and run "
            "`remory init\n"
            f"{exc.name}` again. To set up a different topic, run `remory init "
            "<other>`.\n"
        )
        return msg, 1

    # Backend errors -------------------------------------------------------
    if isinstance(exc, BackendNotFoundError):
        msg = (
            "claude isn't on your PATH.\n"
            "Install Claude Code, or check that the binary is named 'claude'. "
            "Then run remory doctor.\n"
        )
        return msg, 3

    if isinstance(exc, BackendAuthError):
        msg = (
            "claude isn't logged in.\nRun 'claude' once interactively to log in, then try again.\n"
        )
        return msg, 4

    if isinstance(exc, BackendTimeoutError):
        msg = (
            f"claude didn't respond within {exc!s}.\n"
            "Try again. If it persists, check your connection and run remory doctor.\n"
        )
        return msg, 5

    if isinstance(exc, BackendInvocationError):
        tail = _truncate_stderr(exc.stderr_tail)
        head = f"claude exited with code {exc.exit_code}.\n"
        body = f"\n{tail}\n" if tail else ""
        rem = f"Run remory doctor. Full logs at {logs}.\n"
        return head + body + rem, 5

    if isinstance(exc, BackendOutputError):
        msg = (
            "claude returned output I couldn't parse.\n"
            f"Rare; try again. If it persists, file a bug with the logs at {logs}.\n"
        )
        return msg, 5

    # Locking --------------------------------------------------------------
    if isinstance(exc, LockBusyError):
        topic_name = exc.topic_name or "this topic"
        msg = (
            f"Another remory operation is in progress for topic '{topic_name}'.\n"
            "Wait for it to finish, then try again. If nothing else is "
            "running, run remory doctor — there may be a stale .lock.\n"
        )
        return msg, 6

    # Sleep pipeline -------------------------------------------------------
    if isinstance(exc, SleepError):
        if exc.stage == "extract":
            topic_name = exc.state_path.parent.name
            msg = (
                f"Sleep couldn't read what was new in {topic_name!r}.\n"
                f"Re-run 'remory sleep {topic_name}'. If it persists, run remory doctor.\n"
            )
            return msg, 7
        if exc.stage == "merge":
            topic_name = exc.state_path.parent.name
            backup = (
                f" Your data is safe — backup at {exc.backup_path}."
                if exc.backup_path is not None
                else ""
            )
            msg = (
                f"Sleep stopped while merging {topic_name!r}.{backup}\n"
                f"Re-run 'remory sleep {topic_name}'. If it persists, run remory doctor.\n"
            )
            return msg, 7
        if exc.stage == "critique":
            # R3 contract reminder: should never reach the CLI; orchestrator
            # converts to SleepResult.warnings. Treat as a no-op success.
            return ("", 0)

    if isinstance(exc, ExtractError):
        msg = (
            "The model returned text that wasn't valid extraction output, twice. "
            "That's unusual.\n"
            "Try 'remory sleep <topic>' once more. If the same thing happens, "
            "run remory doctor.\n"
        )
        return msg, 7

    if isinstance(exc, MergeError):
        msg = (
            "Sleep failed to merge a section. This is a bug.\n"
            f"File an issue with the logs at {logs}.\n"
        )
        return msg, 7

    if isinstance(exc, CritiqueError):
        # R3: should never reach the CLI. Orchestrator converts to
        # SleepResult.warnings. This row is a contract reminder — if the
        # CLI ever sees CritiqueError, that's an orchestrator bug.
        _log.error(
            "format_error: saw CritiqueError; orchestrator should have converted "
            "it to SleepResult.warnings",
        )
        return ("", 0)

    # Data-parse / metadata ------------------------------------------------
    if isinstance(exc, TopicMetaError):
        topic_name = exc.source.parent.name
        msg = (
            f"Couldn't read meta.yaml for {topic_name!r}: {exc!s}.\n"
            "Run remory doctor — it'll point at the line.\n"
        )
        return msg, 8

    if isinstance(exc, StateParseError):
        # We don't always know which topic from a bare StateParseError;
        # keep the message generic but useful.
        msg = (
            f"Couldn't read state.md: {exc!s}.\n"
            f"Run remory doctor. The most recent backup is in <topic>/.backups.\n"
        )
        return msg, 8

    if isinstance(exc, SchemaError):
        # SchemaError stores ``f"{message} (in {source})"`` in its args.
        # init_cmd composes the rich "Did you mean" + Available block;
        # strip the trailing source suffix for the user-facing surface.
        full = str(exc.args[0]) if exc.args else f"Unknown schema {exc.source!r}."
        suffix = f" (in {exc.source})"
        body = full[: -len(suffix)] if full.endswith(suffix) else full
        return body + "\n", 2

    # Raw / Config ---------------------------------------------------------
    if isinstance(exc, RawWriteError):
        msg = (
            f"Couldn't write a new raw entry: {exc!s}.\n"
            "Disk full or permissions issue. Run remory doctor.\n"
        )
        return msg, 1

    if isinstance(exc, paths.DataDirInsideSourceTreeError):
        msg = (
            f"Data directory {exc.candidate} is inside the Remory source tree.\n"
            "Refusing to use it — your conversation transcripts would land in git.\n"
            "Unset REMORY_DATA_DIR (or point it outside the repo) and try again.\n"
        )
        return msg, 9

    if isinstance(exc, ConfigError):
        path_part = f" at {exc.source}" if exc.source is not None else ""
        edit_hint = f"Edit {exc.source} by hand" if exc.source is not None else "Edit it by hand"
        msg = (
            f"Your config.toml has a problem{path_part}: {exc.validation_error}.\n"
            f"{edit_hint}, or remove it to fall back to defaults. "
            "Run remory doctor afterwards.\n"
        )
        return msg, 9

    # Keyboard / catch-all -------------------------------------------------
    if isinstance(exc, KeyboardInterrupt):
        return ("", 130)

    if isinstance(exc, Exception):
        msg = f"Something unexpected went wrong: {exc!r}.\nFile a bug with the logs at {logs}.\n"
        return msg, 99

    # Unreachable in practice — BaseException not in the above branches.
    return (f"Fatal: {exc!r}\n", 99)
