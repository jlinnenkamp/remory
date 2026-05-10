"""Path resolution for Remory.

Pure path computation: every public function returns a :class:`pathlib.Path`,
none of them touch the filesystem. Directory creation belongs to the callers
that own the lifecycle of the data directory (e.g. ``remory init``).

Resolution precedence:

* ``REMORY_DATA_DIR`` / ``REMORY_CONFIG_DIR`` / ``REMORY_STATE_DIR`` env vars
  win when set and non-empty.
* Otherwise, fall back to the XDG-aware ``platformdirs`` defaults.

The single config-aware resolver for the data directory lives in
:mod:`remory.config` (``resolve_data_dir``). This module is intentionally
unaware of the config file so that the path layer has no upward dependency
on configuration parsing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import platformdirs

__all__ = [
    "backups_dir",
    "claude_md_file",
    "config_dir",
    "data_dir",
    "logs_dir",
    "meta_file",
    "raw_year_dir",
    "review_file",
    "state_dir",
    "state_file",
    "topic_dir",
    "topics_dir",
    "validate_topic_name",
]

# Topic names: lowercase ASCII letters, digits, ``-``, ``_``; must start with
# letter or digit. Mirrors the constraint in §2 of INSTRUCTIONS.md and keeps
# topic directories portable across case-insensitive filesystems.
_TOPIC_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def validate_topic_name(name: str) -> None:
    """Reject names that would be unsafe or non-portable on disk.

    Raises:
        ValueError: if ``name`` is empty, contains ``..``, contains a path
            separator, or fails the lowercase kebab/snake-case pattern.
    """
    if not name:
        raise ValueError("topic name is empty")
    if ".." in name:
        raise ValueError(f"topic name {name!r} contains '..'")
    if "/" in name or "\\" in name:
        raise ValueError(f"topic name {name!r} contains a path separator")
    if not _TOPIC_NAME_RE.match(name):
        raise ValueError(
            f"topic name {name!r} must match {_TOPIC_NAME_RE.pattern!r} "
            "(lowercase ASCII, digits, '-', '_'; must start with letter or digit)"
        )


# Backward-compat alias for callers that imported the private form
# (Phase 1a). New callers should prefer the public name.
_validate_topic_name = validate_topic_name


def data_dir() -> Path:
    """Return the data directory root.

    ``$REMORY_DATA_DIR`` (when set and non-empty) wins; otherwise
    ``platformdirs.user_data_path("remory")``.
    """
    env = os.environ.get("REMORY_DATA_DIR")
    if env:
        return Path(env)
    return platformdirs.user_data_path("remory")


def config_dir() -> Path:
    """Return the config directory root.

    ``$REMORY_CONFIG_DIR`` (when set and non-empty) wins; otherwise
    ``platformdirs.user_config_path("remory")``.
    """
    env = os.environ.get("REMORY_CONFIG_DIR")
    if env:
        return Path(env)
    return platformdirs.user_config_path("remory")


def state_dir() -> Path:
    """Return the state directory root.

    ``$REMORY_STATE_DIR`` (when set and non-empty) wins; otherwise
    ``platformdirs.user_state_path("remory")``.
    """
    env = os.environ.get("REMORY_STATE_DIR")
    if env:
        return Path(env)
    return platformdirs.user_state_path("remory")


def topics_dir() -> Path:
    """Return ``<data_dir>/topics``."""
    return data_dir() / "topics"


def topic_dir(name: str) -> Path:
    """Return ``<topics_dir>/<name>`` after validating ``name``."""
    _validate_topic_name(name)
    return topics_dir() / name


def raw_year_dir(topic_dir: Path, year: int) -> Path:
    """Return ``<topic_dir>/raw/<year>``."""
    return topic_dir / "raw" / str(year)


def backups_dir(topic_dir: Path) -> Path:
    """Return ``<topic_dir>/.backups``."""
    return topic_dir / ".backups"


def logs_dir() -> Path:
    """Return ``<state_dir>/logs``."""
    return state_dir() / "logs"


def state_file(topic_dir: Path) -> Path:
    """Return ``<topic_dir>/state.md``."""
    return topic_dir / "state.md"


def meta_file(topic_dir: Path) -> Path:
    """Return ``<topic_dir>/meta.yaml``."""
    return topic_dir / "meta.yaml"


def review_file(topic_dir: Path) -> Path:
    """Return ``<topic_dir>/_review.md``."""
    return topic_dir / "_review.md"


def claude_md_file(topic_dir: Path) -> Path:
    """Return ``<topic_dir>/CLAUDE.md``."""
    return topic_dir / "CLAUDE.md"
