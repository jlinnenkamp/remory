"""Contract tests asserting the top-level docs are present and structurally intact.

These tests fail loudly if a refactor silently drops the data-and-privacy section
from README, or if architecture.md / schemas.md goes missing. They do NOT lint
content beyond H2 presence — the docs evolve faster than a strict-content test
would tolerate.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


REQUIRED_README_H2S: tuple[str, ...] = (
    "## Why Remory exists",
    "## Quickstart",
    "## Built-in topics",
    "## Data and privacy",
    "## Architecture",
    "## What this is not",
    "## Contributing",
    "## License",
)


def test_readme_has_all_required_h2_headings_per_spec_section_13() -> None:
    """README.md contains every H2 the spec §13 README skeleton requires, in order."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    last_index = -1
    for heading in REQUIRED_README_H2S:
        index = readme.find(heading)
        assert index != -1, f"missing required README H2: {heading!r}"
        assert index > last_index, (
            f"README H2 {heading!r} appears out of order "
            "(expected after the previous required heading)"
        )
        last_index = index


def test_architecture_doc_exists_and_is_nonempty() -> None:
    """docs/architecture.md exists and has non-trivial content (>1 KB)."""
    path = REPO_ROOT / "docs" / "architecture.md"
    assert path.exists(), "docs/architecture.md is missing"
    assert path.stat().st_size > 1024, "docs/architecture.md is suspiciously small"


def test_schemas_doc_exists_and_is_nonempty() -> None:
    """docs/schemas.md exists and has non-trivial content (>1 KB)."""
    path = REPO_ROOT / "docs" / "schemas.md"
    assert path.exists(), "docs/schemas.md is missing"
    assert path.stat().st_size > 1024, "docs/schemas.md is suspiciously small"
