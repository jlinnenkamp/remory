"""Bundled Claude Code templates installed into the user's data directory.

The ``.claude/`` subtree under this package mirrors what `remory init` and
`remory init --refresh` materialise into ``<data_dir>/.claude/``. Files
are exposed via :mod:`importlib.resources`; callers should never read
them via filesystem paths.

Versioning: each markdown template carries an HTML-comment stamp
``<!-- remory: template_version=1 -->`` immediately after the YAML
frontmatter. ``settings.json`` carries a top-level
``"_remory_template_version": 1`` key. Bumping the integer is
forward-compat; renaming the key or removing it requires a migration
plan analogous to :class:`remory.raw.RawStatus`.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Iterator
from pathlib import PurePosixPath

__all__ = ["iter_template_relpaths", "read_template_bytes"]


# Relative POSIX paths (under .claude/) of every bundled template, in
# lex order. Pinned here so callers iterate a single canonical list.
_TEMPLATE_RELPATHS: tuple[str, ...] = (
    ".claude/agents/critic.md",
    ".claude/agents/extractor.md",
    ".claude/agents/merger.md",
    ".claude/agents/wizard.md",
    ".claude/commands/recent.md",
    ".claude/commands/review.md",
    ".claude/commands/sleep.md",
    ".claude/commands/state.md",
    ".claude/settings.json",
)


def iter_template_relpaths() -> Iterator[str]:
    """Yield each bundled template's relative POSIX path under the data dir."""
    yield from _TEMPLATE_RELPATHS


def read_template_bytes(relpath: str) -> bytes:
    """Return the bundled bytes for ``relpath`` (POSIX, relative to data_dir).

    Raises FileNotFoundError if the path is not in the bundled set.
    """
    if relpath not in _TEMPLATE_RELPATHS:
        raise FileNotFoundError(f"unknown bundled template: {relpath!r}")
    parts = PurePosixPath(relpath).parts
    resource = importlib.resources.files("remory.data_templates")
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_bytes()
