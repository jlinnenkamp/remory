"""Shared text templates for Remory.

Phase 5 introduced this module to host the 3-line ``CLAUDE.md``
placeholder used by ``commands/init_cmd.py`` and ``wizard/_commit.py``.

**Phase 6 deprecates this constant.** The real per-topic ``CLAUDE.md``
generator now lives in :mod:`remory.topic_claude_md` and is the only
writer of new per-topic ``CLAUDE.md`` files. Both call sites
(``init_cmd.py`` and ``wizard/_commit.py``) have been migrated.

``CLAUDE_MD_PLACEHOLDER`` is kept exported here for one release to avoid
breaking any external importer; it will be removed in v0.2. New callers
must use :func:`remory.topic_claude_md.render`. Do NOT reach for this
constant when adding code.
"""

from __future__ import annotations

from typing import Final

__all__ = ["CLAUDE_MD_PLACEHOLDER"]


# DEPRECATED (Phase 6): use ``remory.topic_claude_md.render`` instead.
# Kept as an alias for one release. The bytes below are the Phase 5
# 3-line placeholder; they are NOT what new per-topic CLAUDE.md files
# look like in Phase 6 (those carry the §5.7 template + stamp).
CLAUDE_MD_PLACEHOLDER: Final[str] = (
    "# Topic: {schema_name}\n"
    "Do not edit state.md. It is updated only during sleep.\n"
    "See state.md for the canonical context for this topic.\n"
)
