"""Unit tests for ``remory.schema``."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from remory.schema import (
    BUILTIN_NAMES,
    Schema,
    SchemaError,
    _strict_validate,
    iter_builtin,
    load_builtin,
    load_user,
)


def _is_editable_install() -> bool:
    """Detect whether ``remory.schemas_builtin`` resolves into the source tree.

    In an editable install (``uv sync`` of a workspace project), the resource
    path lives in ``src/remory/schemas_builtin``; in a wheel install it lives
    inside ``site-packages``. The ``test_all_builtins_load_from_installed_tree``
    variant only carries weight in the latter case.
    """
    p = str(importlib.resources.files("remory.schemas_builtin"))
    return "site-packages" not in p


# ---------------------------------------------------------------------------
# Built-in load smoke tests
# ---------------------------------------------------------------------------


def test_all_builtins_load() -> None:
    for name in BUILTIN_NAMES:
        schema = load_builtin(name)
        assert isinstance(schema, Schema)
        assert schema.name == name


@pytest.mark.skipif(
    _is_editable_install(),
    reason="editable install --- substring assertion only meaningful in wheel install",
)
def test_all_builtins_load_from_installed_tree() -> None:
    for name in BUILTIN_NAMES:
        schema = load_builtin(name)
        assert isinstance(schema, Schema)
        resource = importlib.resources.files("remory.schemas_builtin").joinpath(f"{name}.yaml")
        assert "src/remory/schemas_builtin" not in str(resource), (
            f"{name} resolved into source tree: {resource!r}"
        )


# ---------------------------------------------------------------------------
# Per-built-in specifics
# ---------------------------------------------------------------------------


def test_builtin_section_specifics() -> None:
    job = load_builtin("job-profile")
    assert [s.id for s in job.sections] == [
        "skills_and_strengths",
        "values_and_priorities",
        "hard_constraints",
        "options_considered",
        "current_leaning",
        "evidence_log",
    ]
    # ``evidence_log`` is the only append_only section in job-profile.
    assert {s.id for s in job.sections if s.append_only} == {"evidence_log"}

    workout = load_builtin("workout")
    assert [s.id for s in workout.sections] == [
        "current_plan",
        "recent_sessions",
        "progressions",
        "notes_and_injuries",
        "goals",
    ]
    assert all(not s.append_only for s in workout.sections)

    coaching = load_builtin("coaching")
    assert [s.id for s in coaching.sections] == [
        "ongoing_themes",
        "insights_by_theme",
        "open_questions",
        "breakthroughs",
        "action_items",
    ]


def test_builtin_defaults_specifics() -> None:
    job = load_builtin("job-profile")
    assert job.defaults.tone == "warm"
    assert job.defaults.strictness == "balanced"
    assert job.sleep.default_depth == "merge_and_critique"
    assert job.sleep.trigger_threshold == 3

    workout = load_builtin("workout")
    assert workout.defaults.tone == "direct"
    assert workout.defaults.strictness == "balanced"
    assert workout.sleep.default_depth == "single_pass"

    coaching = load_builtin("coaching")
    assert coaching.defaults.tone == "warm"
    assert coaching.defaults.strictness == "gentle"
    assert coaching.sleep.default_depth == "merge_and_critique"


def test_iter_builtin_yields_all_three() -> None:
    items = list(iter_builtin())
    assert {name for name, _ in items} == BUILTIN_NAMES


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_load_builtin_unknown_name_raises_SchemaError() -> None:
    with pytest.raises(SchemaError):
        load_builtin("nope")


def test_load_user_extra_top_level_key_is_ignored(tmp_path: Path) -> None:
    """User-side extras-handling is ``ignore`` (asymmetric vs built-in)."""
    src = _minimal_user_yaml() + "extra_top_level_key: tolerated\n"
    p = tmp_path / "custom.yaml"
    p.write_text(src, encoding="utf-8")
    schema = load_user(p)
    assert schema.name == "custom"
    # Unknown key is dropped, not preserved as an attribute.
    assert not hasattr(schema, "extra_top_level_key")


def test_load_builtin_extra_top_level_key_raises_SchemaError() -> None:
    """Strict path catches an unknown top-level key.

    Tested via the public ``_strict_validate`` seam; we assemble a minimal
    Schema-shaped dict and add a stray top-level key, then assert the
    strict pass rejects it. (Directly shimming ``importlib.resources`` would
    work too; the seam is cleaner.)
    """
    data = _minimal_dict()
    data["sneaky_top_level"] = "no"
    with pytest.raises(SchemaError, match=r"unknown key.*sneaky_top_level"):
        _strict_validate(data, Schema)


def test_load_builtin_extra_deeply_nested_key_raises_SchemaError() -> None:
    """Strict path recurses into list elements and nested models.

    The unknown key sits at ``wizard_questions[0].options[0]`` --- a
    three-level nested path. The strict validator must walk into the list
    and into each ``WizardOption`` to catch it.
    """
    data = _minimal_dict()
    data["wizard_questions"] = [
        {
            "id": "tone",
            "question": "x",
            "options": [
                {"value": "a", "label": "A", "typo": "no"},
                {"value": "b", "label": "B"},
            ],
        }
    ]
    with pytest.raises(SchemaError, match=r"wizard_questions\[0\].options\[0\].*typo"):
        _strict_validate(data, Schema)


def test_section_id_uniqueness_violation_raises() -> None:
    data = _minimal_dict()
    data["sections"] = [
        {"id": "alpha", "title": "Alpha", "description": "x"},
        {"id": "alpha", "title": "Alpha 2", "description": "y"},
    ]
    with pytest.raises(ValidationError):
        Schema.model_validate(data)


def test_section_id_regex_rejects_uppercase_and_kebab() -> None:
    for bad_id in ("Alpha", "alpha-beta", "Alpha-Beta", "ALPHA"):
        data = _minimal_dict()
        data["sections"] = [{"id": bad_id, "title": "T", "description": "d"}]
        with pytest.raises(ValidationError):
            Schema.model_validate(data)


def test_wizard_question_id_accepts_user_authored_ids(tmp_path: Path) -> None:
    """Open id space: user-authored knobs beyond tone/strictness are valid."""
    yaml_text = _minimal_user_yaml() + (
        "wizard_questions:\n"
        "  - id: depth_of_engagement\n"
        "    question: how deep?\n"
        "    options:\n"
        "      - value: shallow\n"
        "        label: Shallow\n"
        "      - value: deep\n"
        "        label: Deep\n"
    )
    p = tmp_path / "custom.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    schema = load_user(p)
    assert schema.wizard_questions[0].id == "depth_of_engagement"


# ---------------------------------------------------------------------------
# Helpers for synthetic schemas
# ---------------------------------------------------------------------------


def _minimal_dict() -> dict[str, object]:
    """Return the smallest dict that ``Schema.model_validate`` accepts."""
    return {
        "name": "custom",
        "version": 1,
        "description": "A description.",
        "persona": "A persona.",
        "sections": [{"id": "alpha", "title": "Alpha", "description": "First section."}],
    }


def _minimal_user_yaml() -> str:
    """Return YAML text equivalent to ``_minimal_dict()``."""
    return yaml.safe_dump(_minimal_dict(), sort_keys=False)
