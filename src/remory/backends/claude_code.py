"""Subprocess-driven backend that wraps the local ``claude`` CLI.

Side-effect rule: this backend writes **no** files into the user's data
directory. Its only side effect is whatever the ``claude`` binary itself
writes (transcripts under ``~/.claude/projects/``).

Logging: stdlib ``logging`` only --- no ``print()``. The logger name is
``remory.backends.claude_code``.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from remory import transcripts
from remory.backends.base import (
    BackendInvocationError,
    BackendNotFoundError,
    BackendOutputError,
    BackendTimeoutError,
    ChatResult,
    HeadlessMeta,
    HeadlessResult,
    HealthReport,
)

__all__ = ["ClaudeCodeBackend"]


_log = logging.getLogger("remory.backends.claude_code")


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[:n] + "..."


class ClaudeCodeBackend:
    """Default backend: subprocess to local ``claude`` CLI.

    Stateless across calls (Protocol rule 4). Constructor takes optional
    overrides for the binary name and environment; both default to the
    parent process's defaults.
    """

    def __init__(
        self,
        *,
        binary: str = "claude",
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._binary = binary
        # Stored as ``dict[str, str] | None``; ``None`` means inherit parent env.
        self._env: dict[str, str] | None = dict(env) if env is not None else None

    # ------------------------------------------------------------------ chat

    def chat(
        self,
        *,
        cwd: Path,
        resume: bool = False,
        agent: str | None = None,
        initial_prompt: str | None = None,
    ) -> ChatResult:
        """Launch interactive ``claude`` session in ``cwd``, blocking until exit.

        ``resume=True`` passes ``--resume`` (the underlying ``claude`` flag);
        the user-facing remory flag is ``--continue``, but we use
        ``--resume`` here because that is what ``claude`` itself accepts.

        ``agent`` selects a Claude Code subagent (e.g. ``"wizard"``).
        Passes ``--agent <name>`` when set; ``None`` means no agent flag,
        which is the chat_cmd default.

        ``initial_prompt`` is appended as the trailing positional arg to
        ``claude``, which seeds the session with a first turn from the
        user-side (the model responds to it before yielding the prompt
        back). Used by the wizard launcher to communicate the run-
        directory path and a "begin the interview" instruction in one go,
        so the subagent has both context and a kick-off without the user
        having to type anything first.
        """
        argv: list[str] = [self._binary]
        if agent is not None:
            argv.extend(["--agent", agent])
        if resume:
            argv.append("--resume")
        if initial_prompt is not None:
            argv.append(initial_prompt)

        _log.info(
            "chat: argv=%r cwd=%s resume=%s agent=%s initial_prompt=%s",
            argv,
            cwd,
            resume,
            agent,
            "<set>" if initial_prompt is not None else None,
        )

        start = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                check=False,
                env=self._env,
            )
        except FileNotFoundError as exc:
            raise BackendNotFoundError(f"claude binary {self._binary!r} not on PATH") from exc
        duration = time.monotonic() - start

        transcript_path = transcripts.locate_latest(cwd)
        session_id: str | None = None
        if transcript_path is not None:
            try:
                first = next(iter(transcripts.iter_events(transcript_path)), None)
            except transcripts.TranscriptParseError as exc:
                _log.debug("chat: could not parse transcript %s: %s", transcript_path, exc)
                first = None
            if first is not None:
                session_id = first.session_id

        return ChatResult(
            exit_code=completed.returncode,
            session_id=session_id,
            transcript_path=transcript_path,
            duration_seconds=duration,
            cwd=cwd,
        )

    # -------------------------------------------------------------- headless

    def headless(
        self,
        *,
        prompt: str,
        agent: str | None = None,
        cwd: Path | None = None,
        json_output: bool = False,
        timeout_seconds: int = 600,
    ) -> HeadlessResult:
        """Single non-interactive invocation. Non-streaming."""
        argv: list[str] = [self._binary, "-p", prompt]
        if agent is not None:
            argv.extend(["--agent", agent])
        if json_output:
            argv.extend(["--output-format", "json"])

        _log.info(
            "headless: binary=%s prompt=%r agent=%s json=%s cwd=%s timeout=%d",
            self._binary,
            _truncate(prompt, 80),
            agent,
            json_output,
            cwd,
            timeout_seconds,
        )
        _log.debug("headless: full prompt=%r", prompt)

        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=self._env,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendTimeoutError(
                f"claude headless timed out after {timeout_seconds}s"
            ) from exc
        except FileNotFoundError as exc:
            raise BackendNotFoundError(f"claude binary {self._binary!r} not on PATH") from exc

        if completed.returncode != 0:
            stderr_tail = (completed.stderr or "")[-2048:]
            raise BackendInvocationError(
                f"claude exited with code {completed.returncode}",
                exit_code=completed.returncode,
                stderr_tail=stderr_tail,
            )

        stdout = completed.stdout or ""

        if json_output:
            try:
                envelope: object = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise BackendOutputError(
                    f"claude headless returned non-JSON output: {exc}"
                ) from exc
            if not isinstance(envelope, dict):
                raise BackendOutputError(
                    "claude headless returned a JSON value that is not an object"
                )
            envelope_dict: dict[str, Any] = cast("dict[str, Any]", envelope)

            required = ("result", "session_id", "duration_ms", "num_turns", "stop_reason")
            missing = [k for k in required if k not in envelope_dict]
            if missing:
                raise BackendOutputError(f"claude headless JSON missing required fields: {missing}")
            if envelope_dict.get("is_error") is True:
                raise BackendOutputError(f"is_error envelope: {envelope_dict.get('result', '')}")

            return HeadlessResult(
                text=str(envelope_dict["result"]),
                session_id=(
                    None
                    if envelope_dict["session_id"] is None
                    else str(envelope_dict["session_id"])
                ),
                duration_ms=int(envelope_dict["duration_ms"]),
                num_turns=int(envelope_dict["num_turns"]),
                stop_reason=str(envelope_dict["stop_reason"]),
                meta=HeadlessMeta(raw_envelope=envelope_dict),
            )

        return HeadlessResult(
            text=stdout,
            session_id=None,
            duration_ms=0,
            num_turns=1,
            stop_reason="end_turn",
            meta=HeadlessMeta(raw_envelope=None),
        )

    # ---------------------------------------------------------- health_check

    def health_check(self) -> HealthReport:
        """Phase 2 health check. Does NOT probe authentication."""
        binary_path_str = shutil.which(self._binary)
        if binary_path_str is None:
            return HealthReport(
                binary_present=False,
                binary_path=None,
                version=None,
                authenticated=None,
                notes=("claude binary not on PATH",),
            )

        binary_path = Path(binary_path_str)
        notes: list[str] = []
        version: str | None = None
        try:
            completed = subprocess.run(
                [self._binary, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env=self._env,
            )
            if completed.returncode == 0 and completed.stdout:
                version = completed.stdout.strip() or None
            else:
                notes.append("could not invoke `--version`")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            _log.debug("health_check: --version failed: %s", exc)
            notes.append("could not invoke `--version`")

        notes.append("auth not probed")

        return HealthReport(
            binary_present=True,
            binary_path=binary_path,
            version=version,
            authenticated=None,
            notes=tuple(notes),
        )
