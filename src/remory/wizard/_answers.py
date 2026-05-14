"""Wire-format Pydantic models for wizard answers.

These models describe what the ``wizard.md`` subagent writes to
``<run_dir>/answers.json`` (Phase 6, plan §6.1). ``version`` is the
forward-compat hook per memory ``feedback_wire_format_enums``:
bumping the integer is forward-compatible; renaming the key or any
literal value requires a migration plan analogous to
:class:`remory.raw.RawStatus`.

Both models are ``frozen=True`` and ``extra="forbid"`` — the subagent's
output is contractual, and a stray key must surface as a validation
failure (caught by the orchestrator's parse step, which triggers the
one-shot repair round).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = ["WizardAnswers", "WizardKnobs"]


class WizardKnobs(BaseModel):
    """Per-topic tone and strictness, drawn from the schema's Literal sets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tone: Literal["warm", "balanced", "direct"]
    strictness: Literal["gentle", "balanced", "rigorous"]


class WizardAnswers(BaseModel):
    """Wire-format answer surface written by the wizard subagent.

    ``version`` is the forward-compat hook; bumping it requires a
    migration plan analogous to :class:`remory.raw.RawStatus`.

    ``knobs_by_topic`` is keyed by topic name (the schema ``name`` field,
    e.g. ``"workout"``); a missing entry for a chosen topic means
    "fall back to the schema's defaults". ``chosen_topics`` is a tuple
    (frozen) so the wire-shape stays immutable post-parse; ordering is
    selection order, not lex.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    name: str | None
    chosen_topics: tuple[str, ...]
    knobs_by_topic: dict[str, WizardKnobs]
    wish: str | None
