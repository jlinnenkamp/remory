"""Subagent run-directory handoff for the Phase 6 wizard.

The Python harness owns:

- ``stage_run_dir`` — write the built-in topic schemas and a
  ``manifest.json`` index into a tempdir so the ``wizard.md`` subagent
  has them available via ``Read`` (no claude-side network or filesystem
  scan).
- ``parse_run_dir`` — read back ``answers.json`` + ``letter.md`` after
  the subagent exits, validate against the
  :class:`remory.wizard._answers.WizardAnswers` Pydantic shape, and
  produce a :class:`SubagentRunResult` for the orchestrator's COMMIT
  step.
- ``dump_recovery`` — when validation fails for the second time, persist
  whatever the subagent produced under
  ``<data_dir>/.remory/wizard-recovery/<utc-iso>/`` so nothing the user
  said disappears silently
  (memory ``feedback_no_silent_data_loss``).

Wire-format pins live in :mod:`remory.wizard._answers`; this module is
the I/O surface that the orchestrator drives.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from remory.atomic import atomic_write_bytes
from remory.schema import BUILTIN_NAMES, load_builtin
from remory.wizard._answers import WizardAnswers

__all__ = [
    "ANSWERS_FILE_NAME",
    "LETTER_FILE_NAME",
    "MANIFEST_FILE_NAME",
    "REPAIR_PROMPT_FILE_NAME",
    "SCHEMAS_SUBDIR",
    "SubagentRunResult",
    "WizardAnswerParseError",
    "dump_recovery",
    "parse_run_dir",
    "stage_run_dir",
]

_log = logging.getLogger("remory.wizard.subagent")


# Canonical run-directory layout. These constants exist so test fixtures
# can pin them without re-typing magic strings; the wizard.md subagent
# template hard-codes the same names verbatim (see plan §5.1).
SCHEMAS_SUBDIR: str = "schemas"
MANIFEST_FILE_NAME: str = "manifest.json"
ANSWERS_FILE_NAME: str = "answers.json"
LETTER_FILE_NAME: str = "letter.md"
REPAIR_PROMPT_FILE_NAME: str = "repair_prompt.txt"


@dataclass(frozen=True)
class SubagentRunResult:
    """Successful parse of ``answers.json`` + ``letter.md`` from the run dir."""

    answers: WizardAnswers
    letter: str


class WizardAnswerParseError(Exception):
    """Raised when the wizard subagent's run-dir output cannot be parsed.

    Carries ``message`` for embedding into a repair-round prompt the
    subagent can read, and ``kind`` for diagnostic logging. The CLI
    surface does NOT render the raw message — see ``cli/errors.py``,
    which maps this to a recovery-dir pointer.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Literal["missing", "invalid_json", "validation"],
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind


# ---------------------------------------------------------------------------
# stage_run_dir
# ---------------------------------------------------------------------------


def _builtin_schema_bytes(name: str) -> bytes:
    """Return the bundled YAML bytes for the given built-in schema name.

    Uses :mod:`importlib.resources` indirectly via :func:`load_builtin`
    + a re-read; we re-read the raw bytes (rather than re-serialising
    the loaded model) so the subagent sees the schema exactly as the
    bundled file ships it (comments included).
    """
    # Validate the name is a known built-in (raises SchemaError if not).
    load_builtin(name)
    import importlib.resources

    resource = importlib.resources.files("remory.schemas_builtin").joinpath(f"{name}.yaml")
    return resource.read_bytes()


def stage_run_dir(run_dir: Path) -> None:
    """Materialise ``schemas/`` and ``manifest.json`` under ``run_dir``.

    Layout produced:

        <run_dir>/schemas/<name>.yaml     # one per built-in
        <run_dir>/manifest.json            # JSON array of schema names, lex-sorted

    Built-in schema names come from :data:`remory.schema.BUILTIN_NAMES`
    in lexicographic order. The manifest is written as a top-level JSON
    array (not a dict) — minimal surface, matches the wizard.md
    template's read instructions.
    """
    schemas_dir = run_dir / SCHEMAS_SUBDIR
    schemas_dir.mkdir(parents=True, exist_ok=True)

    names_sorted = sorted(BUILTIN_NAMES)
    for name in names_sorted:
        target = schemas_dir / f"{name}.yaml"
        atomic_write_bytes(target, _builtin_schema_bytes(name))

    manifest_bytes = (json.dumps(names_sorted, indent=2) + "\n").encode("utf-8")
    atomic_write_bytes(run_dir / MANIFEST_FILE_NAME, manifest_bytes)


# ---------------------------------------------------------------------------
# parse_run_dir
# ---------------------------------------------------------------------------


def parse_run_dir(run_dir: Path) -> SubagentRunResult:
    """Read ``answers.json`` + ``letter.md`` from ``run_dir``.

    Raises:
        WizardAnswerParseError: any of:
          * either file is missing (kind="missing"),
          * ``answers.json`` is not valid JSON (kind="invalid_json"),
          * ``answers.json`` fails Pydantic validation
            against :class:`WizardAnswers` (kind="validation").

    Returns a :class:`SubagentRunResult` on success.
    """
    answers_path = run_dir / ANSWERS_FILE_NAME
    letter_path = run_dir / LETTER_FILE_NAME

    if not answers_path.exists():
        raise WizardAnswerParseError(
            f"answers.json is missing at {answers_path}",
            kind="missing",
        )
    if not letter_path.exists():
        raise WizardAnswerParseError(
            f"letter.md is missing at {letter_path}",
            kind="missing",
        )

    try:
        raw_bytes = answers_path.read_bytes()
    except OSError as exc:
        raise WizardAnswerParseError(
            f"could not read answers.json: {exc}",
            kind="missing",
        ) from exc

    try:
        raw_obj: object = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WizardAnswerParseError(
            f"answers.json is not valid JSON: {exc}",
            kind="invalid_json",
        ) from exc

    try:
        answers = WizardAnswers.model_validate(raw_obj)
    except ValidationError as exc:
        raise WizardAnswerParseError(
            f"answers.json failed validation: {exc}",
            kind="validation",
        ) from exc

    try:
        letter_text = letter_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WizardAnswerParseError(
            f"could not read letter.md: {exc}",
            kind="missing",
        ) from exc

    return SubagentRunResult(answers=answers, letter=letter_text)


# ---------------------------------------------------------------------------
# dump_recovery
# ---------------------------------------------------------------------------


def _utc_iso_for_dirname() -> str:
    """UTC ISO timestamp with colons hyphenated (Windows-safe).

    Same convention as :func:`remory.claude_assets.emit_backup` so
    sorting the two backup spaces side-by-side stays consistent.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def dump_recovery(
    data_dir: Path,
    run_dir: Path,
    exc: WizardAnswerParseError,
) -> Path:
    """Persist whatever the subagent produced under a recovery directory.

    Layout:

        <data_dir>/.remory/wizard-recovery/<utc-iso>/
            answers.json.malformed   (when present in run_dir)
            letter.md                 (when present in run_dir)
            validation-error.txt      (always; carries exc.message)

    Uses :func:`remory.atomic.atomic_write_bytes` for every per-file
    write. Returns the recovery directory path so the caller can embed
    it in a user-facing message.
    """
    recovery_root = data_dir / ".remory" / "wizard-recovery"
    recovery_dir = recovery_root / _utc_iso_for_dirname()
    recovery_dir.mkdir(parents=True, exist_ok=True)

    answers_src = run_dir / ANSWERS_FILE_NAME
    if answers_src.exists():
        try:
            data = answers_src.read_bytes()
        except OSError as read_exc:
            _log.warning(
                "could not read answers.json for recovery dump",
                extra={
                    "exception_type": type(read_exc).__name__,
                    "wizard_step": "recovery",
                },
            )
        else:
            atomic_write_bytes(recovery_dir / "answers.json.malformed", data)

    letter_src = run_dir / LETTER_FILE_NAME
    if letter_src.exists():
        try:
            data = letter_src.read_bytes()
        except OSError as read_exc:
            _log.warning(
                "could not read letter.md for recovery dump",
                extra={
                    "exception_type": type(read_exc).__name__,
                    "wizard_step": "recovery",
                },
            )
        else:
            atomic_write_bytes(recovery_dir / "letter.md", data)

    error_bytes = exc.message.encode("utf-8")
    if not error_bytes.endswith(b"\n"):
        error_bytes += b"\n"
    atomic_write_bytes(recovery_dir / "validation-error.txt", error_bytes)

    return recovery_dir
