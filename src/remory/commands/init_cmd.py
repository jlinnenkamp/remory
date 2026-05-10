"""``remory init`` — Phase 4 non-interactive stub.

Behaviour matrix (consolidated plan §3.9):

* ``--schema`` missing → R2 wording, exit 2 (raised as
  :class:`NotImplementedError` by :func:`remory.wizard.run_wizard`; the
  CLI maps it to exit 2 here).
* ``--schema`` unknown → ``SchemaError`` with a ``difflib``-derived
  "Did you mean" hint when there's a close match.
* Topic name invalid → ``ValueError`` from
  :func:`remory.paths._validate_topic_name`; CLI maps to exit 2.
* Topic exists already → :class:`TopicExistsError` (D7), exit 1.
* Otherwise: create ``data_dir``, ``topics_dir``, ``topic_dir`` (under
  the topic lock), write ``meta.yaml`` (with schema defaults for knobs),
  ``state.md`` skeleton, and a 3-line ``CLAUDE.md`` placeholder.

Reads default knob values from the schema's ``defaults`` block. Does
not write ``about-me.md`` (wizard-only).
"""

from __future__ import annotations

import difflib
import logging
import sys
from datetime import UTC, datetime

from remory import config as cfgmod
from remory import paths
from remory.atomic import atomic_write_text
from remory.cli.errors import TopicExistsError
from remory.locking import topic_lock
from remory.paths import validate_topic_name
from remory.schema import BUILTIN_NAMES, SchemaError, load_builtin
from remory.state import StateDoc, StateFrontmatter, StateSection, write_state
from remory.topic import Knobs, TopicMeta, write_meta
from remory.wizard import WIZARD_NOT_BUILT_MESSAGE, WizardNotBuiltError

__all__ = ["run_init"]

_log = logging.getLogger("remory.commands.init")


def _suggest_schema(name: str) -> str | None:
    """Return the closest BUILTIN_NAMES match (or None if too far)."""
    matches = difflib.get_close_matches(name, sorted(BUILTIN_NAMES), n=1, cutoff=0.6)
    return matches[0] if matches else None


def _claude_md_placeholder(schema_name: str) -> str:
    """Three-line CLAUDE.md placeholder per the plan §3.9.

    Phase 6 ships the real generator; until then this is a friendly
    stub so the topic directory is shaped consistently.
    """
    return (
        f"# Topic: {schema_name}\n"
        "Do not edit state.md. It is updated only during sleep.\n"
        "See state.md for the canonical context for this topic.\n"
    )


def run_init(*, topic_name: str, schema_name: str | None) -> None:
    """Create a new topic directory from a built-in schema.

    Args:
        topic_name: the topic directory name; validated via
            :func:`remory.paths._validate_topic_name`.
        schema_name: must be in :data:`remory.schema.BUILTIN_NAMES` for
            the Phase 4 stub. ``None`` raises :class:`WizardNotBuiltError`.

    Raises:
        WizardNotBuiltError: when ``schema_name`` is None.
        ValueError: when ``topic_name`` fails validation.
        SchemaError: when ``schema_name`` is unknown.
        TopicExistsError: when the topic directory already exists.
    """
    if schema_name is None:
        raise WizardNotBuiltError(WIZARD_NOT_BUILT_MESSAGE)

    # Validate topic name early — pure path validation, no I/O.
    validate_topic_name(topic_name)

    if schema_name not in BUILTIN_NAMES:
        # Format the user-facing block here; format_error pipes it through.
        # init_cmd owns the difflib suggestion because it knows the
        # corpus (BUILTIN_NAMES).
        suggestion = _suggest_schema(schema_name)
        available = ", ".join(sorted(BUILTIN_NAMES))
        if suggestion is not None:
            body = (
                f"Unknown schema {schema_name!r}.\n\n"
                f"Did you mean: {suggestion}?\n\n"
                f"Available built-in schemas: {available}."
            )
        else:
            body = f"Unknown schema {schema_name!r}.\n\nAvailable built-in schemas: {available}."
        raise SchemaError(schema_name, body)

    schema = load_builtin(schema_name)

    # Resolve effective data dir and ensure the topics dir exists.
    cfg = cfgmod.load_config()
    data_dir = cfgmod.resolve_data_dir(cfg)
    topics_root = data_dir / "topics"
    topic_dir = topics_root / topic_name

    if topic_dir.exists():
        raise TopicExistsError(name=topic_name, topic_dir=topic_dir)

    topics_root.mkdir(parents=True, exist_ok=True)
    topic_dir.mkdir(parents=True, exist_ok=False)

    now = datetime.now(UTC)

    # Build knobs from the schema's defaults. The Knobs Pydantic model
    # constrains tone/strictness to its own Literal — the schema's
    # SchemaDefaults uses the same Literal sets, so this assignment
    # type-checks.
    knobs = Knobs(tone=schema.defaults.tone, strictness=schema.defaults.strictness)
    meta = TopicMeta(
        schema=schema_name,
        schema_version=schema.version,
        created=now,
        last_consolidated=None,
        last_chat=None,
        pending_count=0,
        total_entries=0,
        knobs=knobs,
    )

    state_doc = StateDoc(
        frontmatter=StateFrontmatter(
            schema=schema_name,
            schema_version=schema.version,
            last_consolidated=None,
            entries_consolidated=0,
        ),
        sections=[StateSection(title=section.title, body="\n") for section in schema.sections],
    )

    with topic_lock(topic_dir, timeout=0.0):
        write_meta(topic_dir, meta)
        write_state(paths.state_file(topic_dir), state_doc)
        atomic_write_text(paths.claude_md_file(topic_dir), _claude_md_placeholder(schema_name))

    sys.stdout.write(
        f"Topic '{topic_name}' created from schema '{schema_name}'.\n"
        f"Try `remory chat {topic_name}` whenever you're ready.\n",
    )
