"""Bundled prompt templates for the sleep pipeline.

Templates use Jinja2 with ``StrictUndefined`` so a missing variable raises at
render time rather than silently producing an empty interpolation. The four
public templates are:

* :data:`EXTRACT_TEMPLATE` -- one call per sleep, given all pending raws.
* :data:`MERGE_TEMPLATE` -- one call per non-append-only section with candidates.
* :data:`MERGE_REVISE_TEMPLATE` -- generate-then-revise sub-pass for
  ``merge_and_critique`` schemas.
* :data:`CRITIQUE_TEMPLATE` -- one call after all merges, produces ``_review.md``.

Section isolation seam: :class:`MergeContext` holds **one** section and one
``current_section_text``. The merger LLM cannot see other sections because
the template never sees them. Do not add an "all_sections" field to
:class:`MergeContext`. The ``test_render_merge_prompt_isolates_section``
regression test exists to catch that.

Import direction: this module is imported by :mod:`remory.sleep.extract`,
:mod:`remory.sleep.merge`, and :mod:`remory.sleep.critique`. To avoid a
cycle, :class:`ExtractCandidate` is referenced via ``TYPE_CHECKING`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jinja2 import Environment, StrictUndefined

from remory.raw import RawEntry
from remory.schema import Schema, SchemaSection
from remory.topic import Knobs

if TYPE_CHECKING:
    from remory.sleep.extract import ExtractCandidate

__all__ = [
    "CRITIQUE_TEMPLATE",
    "EXTRACT_TEMPLATE",
    "MERGE_REVISE_TEMPLATE",
    "MERGE_TEMPLATE",
    "CritiqueContext",
    "ExtractContext",
    "MergeContext",
    "RawForExtract",
    "build_raw_views",
    "render_critique_prompt",
    "render_extract_prompt",
    "render_merge_prompt",
    "render_merge_revise_prompt",
]


_env = Environment(
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


# ---------------------------------------------------------------------------
# Template strings (module-level constants for snapshot tests)
# ---------------------------------------------------------------------------


EXTRACT_TEMPLATE = """\
You are extracting candidate updates from raw conversation entries for the
topic "{{ schema.name }}". Persona context (background only, do not adopt it
as your voice here):

{{ schema.persona }}

Tone knob: {{ knobs.tone }}. Strictness knob: {{ knobs.strictness }}.

The state.md for this topic is divided into the following sections. Each
candidate update you produce MUST be assigned to exactly one of these
section ids:

{% for section in schema.sections %}
- {{ section.id }} ("{{ section.title }}"): {{ section.description }}
{% endfor %}

Below are the raw entries to process. Each raw entry has a path under
``raw/<year>/<file>.md``; cite that path verbatim as the ``evidence`` for
any candidate you derive from it.

{% for entry in raws %}
=== {{ entry.relative_path }} (created {{ entry.created_iso }}) ===

{{ entry.body }}

{% endfor %}

Respond with a JSON object mapping each section id to a list of candidate
updates. Each candidate has the shape:

  {"text": "<one-line statement>", "evidence": "raw/<year>/<file>.md"}

The ``text`` field is the content only. Do NOT prefix dates or
timestamps to it — for append_only sections the harness prepends the
entry's creation date when rendering, so a model-supplied date prefix
appears as a duplicate.

Sections with no candidate updates can be omitted or have an empty list.
Do not include any section ids not listed above.
{% if stricter %}

IMPORTANT: respond with ONLY a JSON object. No prose before or after, no
markdown fences, no commentary. Just the JSON.
{% endif %}
"""


MERGE_TEMPLATE = """\
You are updating one section of state.md. You can see ONLY this section.
Other sections are intentionally hidden from you so your update cannot
drift into them.

Persona (adopt this voice):

{{ persona }}

Tone: {{ knobs.tone }}. Strictness: {{ knobs.strictness }}.

Section: "{{ section.title }}" (id: {{ section.id }}).
Section description: {{ section.description }}

Current section text (may be empty):

<<<CURRENT
{{ current_section_text }}
CURRENT>>>

Candidate updates extracted from recent raw entries, with evidence paths:

{% for candidate in candidates %}
- {{ candidate.text }}  (evidence: {{ candidate.evidence }})
{% endfor %}

Rewrite the section. Integrate the candidate updates into the prose. Keep
existing material that remains accurate. Drop or correct material that the
new evidence contradicts. Address the user in second person. Do NOT include
the section heading -- output only the body of this section, ending with a
single newline.
"""


MERGE_REVISE_TEMPLATE = """\
You just produced a draft for the section "{{ section.title }}". Now revise
it. Check for:

- consistency with the requested tone ({{ knobs.tone }}) and strictness
  ({{ knobs.strictness }})
- claims you cannot support from the candidate evidence
- prose that drifts into other sections (you saw only this section's
  description: {{ section.description }})
- redundancy with the original section text

Original section text:

<<<CURRENT
{{ current_section_text }}
CURRENT>>>

Your draft:

<<<DRAFT
{{ draft }}
DRAFT>>>

Output a revised body for this section only. No heading. End with a single
newline.
"""


CRITIQUE_TEMPLATE = """\
You are reviewing the freshly consolidated state.md for the topic
"{{ schema.name }}". Persona (background only, do not adopt as voice):

{{ schema.persona }}

Tone: {{ knobs.tone }}. Strictness: {{ knobs.strictness }}.

Full state.md text (frontmatter and all sections):

<<<STATE
{{ state_md_text }}
STATE>>>

Produce a markdown review note covering:

1. Possible contradictions across sections.
2. Sections that look thin, stale, or under-supported relative to others.
3. Claims that do not appear to trace back to the evidence log.

Be concrete. Quote section titles. Do not modify state.md -- this output is
written to ``_review.md`` for the user to read on their own time.
"""


# ---------------------------------------------------------------------------
# Frozen contexts (one per template)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawForExtract:
    """Per-raw view passed to the extract template.

    The extract template asks the LLM to cite a relative path; we pre-compute
    that path here so the template stays simple.
    """

    relative_path: str
    created_iso: str
    body: str


@dataclass(frozen=True)
class ExtractContext:
    schema: Schema
    knobs: Knobs
    raws: tuple[RawForExtract, ...]


@dataclass(frozen=True)
class MergeContext:
    """Section-isolation seam.

    Holds exactly one :class:`SchemaSection` plus its ``current_section_text``.
    The merger LLM never sees any other section because this dataclass cannot
    hold any other section. Persona is supplied directly (the merger does not
    receive a full :class:`Schema`).
    """

    section: SchemaSection
    current_section_text: str
    candidates: tuple[ExtractCandidate, ...]
    persona: str
    knobs: Knobs


@dataclass(frozen=True)
class CritiqueContext:
    schema: Schema
    knobs: Knobs
    state_md_text: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_raw_views(raws: tuple[RawEntry, ...]) -> tuple[RawForExtract, ...]:
    """Convert :class:`RawEntry` instances into the lightweight extract view.

    ``relative_path`` is derived from the on-disk shape
    ``<topic_dir>/raw/<year>/<file>.md`` -- the relative POSIX form is
    ``raw/<year>/<file>.md``. The topic dir is implicit; we just use the
    parent directory's name (the year) and the file name.
    """
    views: list[RawForExtract] = []
    for entry in raws:
        rel = f"raw/{entry.path.parent.name}/{entry.path.name}"
        created = entry.frontmatter.created
        if created.tzinfo is None:
            created_iso = created.isoformat() + "Z"
        else:
            created_iso = created.isoformat().replace("+00:00", "Z")
        views.append(
            RawForExtract(
                relative_path=rel,
                created_iso=created_iso,
                body=entry.body,
            )
        )
    return tuple(views)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_extract_prompt(ctx: ExtractContext, *, stricter: bool = False) -> str:
    """Render the extract prompt. ``stricter=True`` adds the JSON-only clamp."""
    template = _env.from_string(EXTRACT_TEMPLATE)
    return template.render(
        schema=ctx.schema,
        knobs=ctx.knobs,
        raws=ctx.raws,
        stricter=stricter,
    )


def render_merge_prompt(ctx: MergeContext) -> str:
    """Render the per-section merge prompt."""
    template = _env.from_string(MERGE_TEMPLATE)
    return template.render(
        persona=ctx.persona,
        knobs=ctx.knobs,
        section=ctx.section,
        current_section_text=ctx.current_section_text,
        candidates=ctx.candidates,
    )


def render_merge_revise_prompt(ctx: MergeContext, draft: str) -> str:
    """Render the generate-then-revise prompt for an already-drafted section."""
    template = _env.from_string(MERGE_REVISE_TEMPLATE)
    return template.render(
        persona=ctx.persona,
        knobs=ctx.knobs,
        section=ctx.section,
        current_section_text=ctx.current_section_text,
        draft=draft,
    )


def render_critique_prompt(ctx: CritiqueContext) -> str:
    """Render the critique prompt."""
    template = _env.from_string(CRITIQUE_TEMPLATE)
    return template.render(
        schema=ctx.schema,
        knobs=ctx.knobs,
        state_md_text=ctx.state_md_text,
    )
