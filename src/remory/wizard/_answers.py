"""WizardAnswers dataclass — accumulator the orchestrator threads.

Lives in its own module so :mod:`remory.wizard._commit` and
:mod:`remory.wizard._steps` can both import it without each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["WizardAnswers"]


@dataclass
class WizardAnswers:
    """In-memory state collected by the wizard before COMMIT.

    Mutable: the orchestrator assigns step results as it goes. A
    field set to ``None`` means "user skipped" (free-text); a missing
    entry in ``knobs_by_topic`` means "user skipped both option
    questions for that topic — fall back to schema defaults".
    """

    name: str | None = None
    chosen_topics: list[str] = field(default_factory=lambda: [])
    knobs_by_topic: dict[str, dict[str, str]] = field(default_factory=lambda: {})
    wish: str | None = None
