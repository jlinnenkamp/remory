"""Topic schema models and loaders.

INSTRUCTIONS §5 says built-in schemas are validated "at package import time".
We interpret this as "before runtime errors can occur, not literally during
import." The CI guarantee is `tests/unit/test_schema.py::test_all_builtins_load`.

Wire-format note: extras-handling is asymmetric --- built-in schemas are
loaded with strict unknown-key rejection; user-authored schemas are loaded
with ``extra="ignore"`` for forward-compat tolerance across ``schema_version``
bumps. **Flipping the user-side from ``ignore`` to ``forbid`` later is a
wire-format change** subject to the same constraints as ``RawStatus``/
``RawSource``: adding a value (extra) is forward-compat-OK with a documented
plan; renaming or removing is a breaking change requiring a migration tool.
"""

from __future__ import annotations

import functools
import importlib.resources
from collections.abc import Iterator
from pathlib import Path
from types import GenericAlias
from typing import Literal, cast, get_args, get_origin

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

__all__ = [
    "BUILTIN_NAMES",
    "Schema",
    "SchemaDefaults",
    "SchemaError",
    "SchemaSection",
    "SchemaSleepPolicy",
    "WizardOption",
    "WizardQuestion",
    "iter_builtin",
    "load_builtin",
    "load_user",
]


BUILTIN_NAMES: frozenset[str] = frozenset({"job-profile", "workout", "coaching"})


class SchemaError(Exception):
    """Wraps schema-load failures with the source path or built-in name."""

    def __init__(self, source: str, message: str) -> None:
        self.source = source
        super().__init__(f"{message} (in {source})")


class SchemaSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    description: str
    append_only: bool = False


class SchemaSleepPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    default_depth: Literal["single_pass", "merge_and_critique"] = "merge_and_critique"
    trigger_threshold: int = Field(ge=1, default=3)


class SchemaDefaults(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tone: Literal["warm", "balanced", "direct"] = "balanced"
    strictness: Literal["gentle", "balanced", "rigorous"] = "balanced"


class WizardOption(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str
    label: str


class WizardQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Open id space: user-authored schemas may introduce knobs beyond
    # tone/strictness. Validation of which knobs the wizard actually
    # acts on is a separate concern.
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    question: str
    options: list[WizardOption] = Field(min_length=2)


class Schema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: int = Field(ge=1)
    description: str
    persona: str
    sections: list[SchemaSection] = Field(min_length=1)
    sleep: SchemaSleepPolicy = SchemaSleepPolicy()
    defaults: SchemaDefaults = SchemaDefaults()
    wizard_questions: list[WizardQuestion] = Field(default_factory=lambda: [])

    @field_validator("sections")
    @classmethod
    def _section_ids_unique(cls, sections: list[SchemaSection]) -> list[SchemaSection]:
        seen: set[str] = set()
        duplicates: list[str] = []
        for section in sections:
            if section.id in seen:
                duplicates.append(section.id)
            seen.add(section.id)
        if duplicates:
            raise ValueError(f"duplicate section ids: {sorted(set(duplicates))}")
        return sections


def _strict_validate(data: object, model_cls: type[BaseModel], path: str = "") -> None:
    """Recursively reject unknown keys against the model's declared fields.

    Raises ``SchemaError`` on the first unknown key encountered. Recurses into
    sub-models (via ``model_fields``) and into homogeneous list fields whose
    element type is itself a ``BaseModel`` subclass.

    ``path`` is the source-side dotted path for diagnostics; the caller wraps
    the raised ``SchemaError`` with the source name.
    """
    if not isinstance(data, dict):
        return
    # ``data`` came from ``yaml.safe_load`` which returns ``Any``-typed
    # nested structures. Narrow to ``dict[str, object]`` for the rest of
    # this function; the runtime ``isinstance`` above guards the cast.
    typed_data: dict[str, object] = cast(dict[str, object], data)

    known = set(model_cls.model_fields.keys())
    # Pydantic alias support: also accept declared aliases as known keys.
    for finfo in model_cls.model_fields.values():
        if finfo.alias is not None:
            known.add(finfo.alias)

    for key in typed_data:
        if key not in known:
            raise SchemaError(
                "<strict-validate>",
                f"unknown key {path + '.' if path else ''}{key!r}",
            )

    for fname, finfo in model_cls.model_fields.items():
        # Look up by alias first (matches YAML key) then by field name.
        lookup_key = finfo.alias if finfo.alias is not None else fname
        if lookup_key in typed_data:
            value: object = typed_data[lookup_key]
        elif fname in typed_data:
            value = typed_data[fname]
        else:
            continue
        annotation = finfo.annotation
        sub_model = _maybe_basemodel(annotation)
        if sub_model is not None and isinstance(value, dict):
            _strict_validate(
                cast(dict[str, object], value),
                sub_model,
                f"{path}.{fname}" if path else fname,
            )
            continue
        elem_model = _maybe_list_of_basemodel(annotation)
        if elem_model is not None and isinstance(value, list):
            elements: list[object] = cast(list[object], value)
            for idx, elem in enumerate(elements):
                if isinstance(elem, dict):
                    _strict_validate(
                        cast(dict[str, object], elem),
                        elem_model,
                        f"{path}.{fname}[{idx}]" if path else f"{fname}[{idx}]",
                    )


def _maybe_basemodel(annotation: object) -> type[BaseModel] | None:
    """Return ``annotation`` if it is a ``BaseModel`` subclass, else ``None``.

    Strips ``Optional[...]`` / ``X | None`` to a single non-None branch when
    that branch is a ``BaseModel`` subclass.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin is None:
        return None
    # Handle ``X | None`` / ``Union[X, None]``.
    args = [a for a in get_args(annotation) if a is not type(None)]
    if len(args) == 1:
        return _maybe_basemodel(args[0])
    return None


def _maybe_list_of_basemodel(annotation: object) -> type[BaseModel] | None:
    """If ``annotation`` is ``list[T]`` where ``T`` is a ``BaseModel``, return ``T``.

    Returns ``None`` otherwise.
    """
    origin = get_origin(annotation)
    # ``list[T]`` shows as origin == list (or builtins.list); GenericAlias on 3.12+.
    if origin is list or (isinstance(annotation, GenericAlias) and get_origin(annotation) is list):
        args = get_args(annotation)
        if args:
            return _maybe_basemodel(args[0])
    return None


def _parse_yaml(text: str, source: str) -> object:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SchemaError(source, f"YAML parse error: {exc}") from exc


@functools.cache
def load_builtin(name: str) -> Schema:
    """Load and validate a built-in schema by name.

    Strict mode: unknown keys at any nesting level cause a ``SchemaError``.
    Result is cached for the lifetime of the process. (Equivalent to
    ``@functools.lru_cache(maxsize=None)``; ``@functools.cache`` is the
    canonical 3.9+ spelling and what ruff's ``UP033`` enforces.)
    """
    if name not in BUILTIN_NAMES:
        raise SchemaError(name, f"unknown built-in schema {name!r}")
    resource = importlib.resources.files("remory.schemas_builtin").joinpath(f"{name}.yaml")
    try:
        text = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise SchemaError(name, f"could not read built-in schema: {exc}") from exc
    data = _parse_yaml(text, name)
    if not isinstance(data, dict):
        raise SchemaError(name, "top-level YAML value must be a mapping")
    typed_data: dict[str, object] = cast(dict[str, object], data)
    _strict_validate(typed_data, Schema)
    try:
        return Schema.model_validate(typed_data)
    except ValidationError as exc:
        raise SchemaError(name, f"validation error: {exc}") from exc


def load_user(path: Path) -> Schema:
    """Load and validate a user-authored schema YAML.

    User schemas are validated with ``extra="ignore"`` (the model defaults)
    for forward-compat tolerance across ``schema_version`` bumps.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(str(path), f"could not read user schema: {exc}") from exc
    data = _parse_yaml(text, str(path))
    if not isinstance(data, dict):
        raise SchemaError(str(path), "top-level YAML value must be a mapping")
    try:
        return Schema.model_validate(data)
    except ValidationError as exc:
        raise SchemaError(str(path), f"validation error: {exc}") from exc


def iter_builtin() -> Iterator[tuple[str, Schema]]:
    """Yield each built-in (name, Schema) pair. Convenience for tests."""
    for name in sorted(BUILTIN_NAMES):
        yield name, load_builtin(name)
