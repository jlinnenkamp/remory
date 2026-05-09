"""Topic metadata and topic-handle types.

This module owns ``meta.yaml`` round-tripping (Pydantic v2) and a small
``Topic`` dataclass that bundles the metadata with its resolved
:class:`remory.schema.Schema`.

Locking discipline: ``write_meta`` asserts ``is_locked`` on the topic
directory. The assertion is a programming-bug check --- it catches a caller
that forgot to acquire the topic lock at all. It is not a defence against a
caller that releases the lock between the check and the atomic rename.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from remory import paths
from remory.atomic import atomic_write_text
from remory.locking import is_locked
from remory.schema import BUILTIN_NAMES, Schema, load_builtin, load_user

__all__ = [
    "Knobs",
    "Topic",
    "TopicMeta",
    "TopicMetaError",
    "load_topic",
    "read_meta",
    "write_meta",
]


class TopicMetaError(Exception):
    """Wraps ``meta.yaml`` parse/validation failures with the source path."""

    def __init__(self, source: Path, message: str) -> None:
        self.source = source
        super().__init__(f"{message} (in {source})")


class Knobs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: Literal["warm", "balanced", "direct"]
    strictness: Literal["gentle", "balanced", "rigorous"]


class TopicMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str = Field(alias="schema", pattern=r"^[a-z][a-z0-9_-]*$")
    schema_version: int = Field(ge=1)
    created: datetime
    last_consolidated: datetime | None = None
    last_chat: datetime | None = None
    pending_count: int = Field(ge=0, default=0)
    total_entries: int = Field(ge=0, default=0)
    knobs: Knobs


@dataclass(frozen=True)
class Topic:
    """A topic's name, directory, parsed metadata, and resolved schema.

    The ``schema`` field name is fine on a dataclass --- Pydantic's reserved-name
    machinery does not apply here.
    """

    name: str
    dir: Path
    meta: TopicMeta
    schema: Schema


def read_meta(topic_dir: Path) -> TopicMeta:
    """Read and validate ``meta.yaml`` in ``topic_dir``."""
    path = paths.meta_file(topic_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TopicMetaError(path, f"could not read meta.yaml: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TopicMetaError(path, f"YAML parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise TopicMetaError(path, "meta.yaml top-level value must be a mapping")
    try:
        return TopicMeta.model_validate(data)
    except ValidationError as exc:
        raise TopicMetaError(path, f"validation error: {exc}") from exc


def _format_meta_yaml(meta: TopicMeta) -> str:
    """Serialise ``meta`` to a canonical YAML string.

    Datetimes are rendered as ISO-8601 with the ``Z`` suffix. Key order
    follows the field declaration order on :class:`TopicMeta`. Unicode is
    preserved; flow style is disabled so values are block-style.
    """
    raw = meta.model_dump(mode="json", by_alias=True)
    # ``mode="json"`` emits ISO-8601 with offset like ``2026-05-09T09:30:00+00:00``;
    # the spec uses ``Z``. Normalise UTC offsets to ``Z`` everywhere.
    normalised = _normalise_z(raw)
    return yaml.safe_dump(
        normalised,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def _normalise_z(value: object) -> object:
    """Recursively rewrite ``+00:00`` datetime strings to ``Z`` form."""
    if isinstance(value, str):
        if value.endswith("+00:00") and "T" in value:
            return value[: -len("+00:00")] + "Z"
        return value
    if isinstance(value, Mapping):
        m: Mapping[object, object] = cast(Mapping[object, object], value)
        return {k: _normalise_z(v) for k, v in m.items()}
    if isinstance(value, list):
        items: list[object] = cast(list[object], value)
        return [_normalise_z(v) for v in items]
    return value


def write_meta(topic_dir: Path, meta: TopicMeta) -> None:
    """Atomically write ``meta`` to ``meta.yaml`` in ``topic_dir``.

    The topic lock must already be held; the assertion is a programming-bug
    check (caller forgot to acquire the lock at all), not a defence against
    a caller that releases between the check and the rename.
    """
    assert is_locked(topic_dir), "write_meta requires the topic lock"
    path = paths.meta_file(topic_dir)
    atomic_write_text(path, _format_meta_yaml(meta))


def _resolve_user_schema_path(name: str) -> Path:
    """Where a user schema by ``name`` would live.

    ``$XDG_CONFIG_HOME/remory/schemas/<name>.yaml``. Resolution does not
    consult the on-disk config file --- only XDG and the env override on
    ``remory.paths.config_dir``.
    """
    return paths.config_dir() / "schemas" / f"{name}.yaml"


def load_topic(topic_dir: Path) -> Topic:
    """Read meta + resolve schema, returning a :class:`Topic`.

    Schema resolution: built-in if ``meta.schema_name`` is in
    :data:`remory.schema.BUILTIN_NAMES`, otherwise loads from the user
    schemas directory at ``$XDG_CONFIG_HOME/remory/schemas/<name>.yaml``.
    """
    meta = read_meta(topic_dir)
    if meta.schema_name in BUILTIN_NAMES:
        schema = load_builtin(meta.schema_name)
    else:
        schema = load_user(_resolve_user_schema_path(meta.schema_name))
    return Topic(name=topic_dir.name, dir=topic_dir, meta=meta, schema=schema)
