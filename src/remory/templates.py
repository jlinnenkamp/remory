"""Shared text templates for Remory.

Phase 5 introduces this module to host strings that more than one
caller writes to disk verbatim. Today: the 3-line ``CLAUDE.md``
placeholder, used by ``commands/init_cmd.py`` (the non-interactive
stub path) and ``wizard/_commit.py`` (the wizard's COMMIT block).

Phase 6 will own the real ``CLAUDE.md`` template per topic; until
then this single placeholder is good enough for the directory shape.
"""

from __future__ import annotations

from typing import Final

__all__ = ["CLAUDE_MD_PLACEHOLDER"]


# Phase 4 + Phase 5 share these bytes verbatim. Tests pin both call
# sites against the same constant so a typo in one side cannot drift
# from the other. Format key: ``schema_name`` is the topic schema name
# (e.g. ``"workout"``).
CLAUDE_MD_PLACEHOLDER: Final[str] = (
    "# Topic: {schema_name}\n"
    "Do not edit state.md. It is updated only during sleep.\n"
    "See state.md for the canonical context for this topic.\n"
)
