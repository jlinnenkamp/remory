"""``state.md`` read/write.

This module commits to **idempotent-after-one-normalisation-pass** round-tripping,
not byte-stable on arbitrary input.

- For documents we produced ourselves: ``render_state(read_state(x)) == x`` byte-for-byte.
- For arbitrary handcrafted input: ``render_state(read_state(render_state(read_state(x))))
  == render_state(read_state(x))`` --- one normalisation pass converges.

Hand-edited ``state.md`` files will see frontmatter formatting canonicalised on next
sleep write (key order, quoting, datetime format). This is acceptable in 1b because
sleep is the only writer.

This module is intentionally schema-agnostic: ``read_state`` does not validate
section titles. The :func:`validate_state` helper is the seam for that.
``state.md`` backup-before-sleep is **not** this module's concern; Phase 3 will
write ``.bak`` files via :mod:`remory.atomic` directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from remory.atomic import atomic_write_text
from remory.locking import is_locked
from remory.schema import Schema

__all__ = [
    "StateDoc",
    "StateFrontmatter",
    "StateParseError",
    "StateSchemaMismatchError",
    "StateSection",
    "read_state",
    "render_state",
    "validate_state",
    "write_state",
]


class StateParseError(Exception):
    """Raised when ``state.md`` is malformed."""


class StateSchemaMismatchError(Exception):
    """Raised when ``state.md`` section titles do not match the schema."""


class StateFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str = Field(alias="schema")
    schema_version: int = Field(ge=1)
    last_consolidated: datetime | None = None
    entries_consolidated: int = Field(ge=0, default=0)


@dataclass(frozen=True)
class StateSection:
    title: str
    body: str


@dataclass
class StateDoc:
    frontmatter: StateFrontmatter
    sections: list[StateSection] = field(default_factory=lambda: [])


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split a state.md text into (frontmatter_yaml, body).

    The opening fence must be the first line. Raises
    :class:`StateParseError` if either fence is missing or out of place.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise StateParseError("state.md must start with a '---' frontmatter fence")
    # Find closing fence.
    close_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            close_idx = i
            break
    if close_idx is None:
        raise StateParseError("state.md is missing the closing '---' frontmatter fence")
    yaml_text = "".join(lines[1:close_idx])
    body = "".join(lines[close_idx + 1 :])
    return yaml_text, body


def _is_fence_line(line: str) -> bool:
    """True if ``line`` opens or closes a fenced code block.

    Recognises bare ``````` and `````lang``-style openings (and tildes too,
    per CommonMark). The line is considered a fence if its left-stripped form
    starts with three or more of the fence character.
    """
    stripped = line.lstrip()
    if stripped.startswith("```"):
        return True
    return stripped.startswith("~~~")


def _split_body_into_sections(body: str) -> list[StateSection]:
    """Walk the body line-by-line, splitting on top-level ``# Title`` headings.

    Heading detection respects fenced-code blocks: while inside a fence,
    lines beginning with ``#`` are body, not headings. Anything before the
    first heading must be empty (whitespace only); otherwise we raise
    :class:`StateParseError`.
    """
    if body == "":
        return []

    lines = body.splitlines(keepends=True)
    in_fence = False
    sections: list[tuple[str, list[str]]] = []
    preamble: list[str] = []
    current: tuple[str, list[str]] | None = None

    for line in lines:
        if _is_fence_line(line):
            in_fence = not in_fence
            if current is None:
                preamble.append(line)
            else:
                current[1].append(line)
            continue

        if not in_fence and line.startswith("# "):
            # New heading. Title is everything after ``# `` up to the line
            # ending. We strip the trailing newline only --- whitespace inside
            # the title is preserved.
            title = line[2:].rstrip("\r\n")
            if current is not None:
                sections.append(current)
            current = (title, [])
            continue

        if current is None:
            preamble.append(line)
        else:
            current[1].append(line)

    if current is not None:
        sections.append(current)

    if any(line.strip() for line in preamble):
        raise StateParseError(
            "state.md body must not contain non-empty content before the first '# ' heading"
        )

    # The renderer emits ``# Title\n\n{body}``; the parser treats the blank
    # line after the heading as part of the section structure, not body. We
    # strip a single leading ``\n`` if present so round-tripping is stable.
    result: list[StateSection] = []
    for title, body_lines in sections:
        body = "".join(body_lines)
        if body.startswith("\n"):
            body = body[1:]
        result.append(StateSection(title=title, body=body))
    return result


def read_state(path: Path) -> StateDoc:
    """Read and parse ``state.md`` from ``path``.

    Schema-agnostic: section titles are not validated against any schema.
    Use :func:`validate_state` for that.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateParseError(f"could not read {path}: {exc}") from exc
    yaml_text, body = _split_frontmatter(text)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise StateParseError(f"frontmatter YAML parse error in {path}: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise StateParseError(f"frontmatter in {path} must be a mapping")
    try:
        frontmatter = StateFrontmatter.model_validate(data)
    except ValidationError as exc:
        raise StateParseError(f"frontmatter validation error in {path}: {exc}") from exc
    sections = _split_body_into_sections(body)
    return StateDoc(frontmatter=frontmatter, sections=sections)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


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


def _render_frontmatter(fm: StateFrontmatter) -> str:
    raw = fm.model_dump(mode="json", by_alias=True)
    normalised = _normalise_z(raw)
    return yaml.safe_dump(
        normalised,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def _normalise_section_body(body: str) -> str:
    """Strip trailing newlines, then ensure exactly one trailing newline.

    Preserves all internal whitespace, including blank lines and unusual
    indentation, by only touching the trailing run of ``\\n`` / ``\\r``.
    """
    return body.rstrip("\r\n") + "\n"


def render_state(doc: StateDoc) -> str:
    """Render a :class:`StateDoc` to its canonical ``state.md`` text.

    Pure: no I/O. The output round-trips through :func:`read_state` byte-stably
    when the input was itself produced by this function.
    """
    parts: list[str] = []
    parts.append("---\n")
    parts.append(_render_frontmatter(doc.frontmatter))
    parts.append("---\n\n")
    rendered_sections: list[str] = []
    for section in doc.sections:
        body = _normalise_section_body(section.body)
        rendered_sections.append(f"# {section.title}\n\n{body}")
    # Sections joined by ``\n`` so each section after the first has a blank
    # line before its leading ``#``. Bodies already end in exactly one newline.
    parts.append("\n".join(rendered_sections))
    return "".join(parts)


def write_state(path: Path, doc: StateDoc) -> None:
    """Atomically write ``doc`` to ``path``.

    The topic lock must already be held; assertion is a programming-bug check.
    """
    assert is_locked(path.parent), "write_state requires the topic lock"
    atomic_write_text(path, render_state(doc))


def validate_state(doc: StateDoc, schema: Schema) -> None:
    """Check that section titles in ``doc`` match ``schema.sections[*].title`` in order.

    Raises :class:`StateSchemaMismatchError` on any mismatch (extra, missing,
    or wrong-order sections).
    """
    expected = [s.title for s in schema.sections]
    actual = [s.title for s in doc.sections]
    if expected != actual:
        raise StateSchemaMismatchError(
            f"state.md sections {actual!r} do not match schema {expected!r}"
        )
