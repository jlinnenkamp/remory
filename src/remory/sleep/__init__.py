"""Sleep pipeline: extract -> merge -> critique.

Public surface re-exported from :mod:`remory.sleep.orchestrator`. Other
sleep submodules (``extract``, ``merge``, ``critique``, ``prompts``) are
internal in Phase 3 -- the orchestrator is the only intended caller from
outside the package.
"""

from remory.sleep.orchestrator import (
    SectionOutcome,
    SleepError,
    SleepResult,
    SleepStatus,
    sleep,
)

__all__ = [
    "SectionOutcome",
    "SleepError",
    "SleepResult",
    "SleepStatus",
    "sleep",
]
