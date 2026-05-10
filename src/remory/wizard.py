"""First-run wizard (Phase 5 implementation).

Phase 4 ships only the stub: :func:`run_wizard` raises
:class:`NotImplementedError` with the R2 user-facing message; the
``init`` command surfaces this as a usage error pointing the user at
the ``--schema`` non-interactive path.

The :class:`WizardAnswers` dataclass shape is forward-debt for Phase 5
and is intentionally tiny — the wizard's atomic-batch COMMIT (D5) will
be filled in then.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "WIZARD_NOT_BUILT_MESSAGE",
    "WizardAnswers",
    "WizardNotBuiltError",
    "commit",
    "run_wizard",
]


# R2 wording (locked) — surfaced by `remory init` when --schema is missing.
WIZARD_NOT_BUILT_MESSAGE = (
    "The interactive wizard isn't built yet. For now, pass --schema to pick a\n"
    "built-in: --schema job-profile, --schema workout, or --schema coaching."
)


class WizardNotBuiltError(Exception):
    """Raised by Phase 4 ``init`` when ``--schema`` is missing.

    Carries the R2 user-facing message; the CLI maps it to exit 2.
    """


@dataclass
class WizardAnswers:
    """In-memory state collected by the wizard before the COMMIT phase.

    Phase 5 fills the body. Phase 4 keeps this importable for tests
    that pin the dataclass shape's existence.
    """

    name: str | None = None
    chosen_topics: list[str] = field(default_factory=lambda: [])
    knobs_by_topic: dict[str, dict[str, str]] = field(default_factory=lambda: {})
    wish: str | None = None


def run_wizard() -> WizardAnswers:
    """Run the interactive wizard. Phase 5 implementation; Phase 4 stub."""
    raise WizardNotBuiltError(WIZARD_NOT_BUILT_MESSAGE)


def commit(answers: WizardAnswers, *, data_dir: Path) -> None:
    """Atomically materialise ``answers`` to disk. Phase 5 implementation."""
    del answers, data_dir
    raise WizardNotBuiltError(WIZARD_NOT_BUILT_MESSAGE)
