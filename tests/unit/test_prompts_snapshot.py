"""Snapshot tests for the bundled sleep prompts.

The golden strings below are the source of truth for the byte content of
each prompt at a *fixed* set of inputs. If you intentionally change a
template, regenerate the snapshot; the diff in review is the audit trail.

Section-isolation regression test: ``test_render_merge_prompt_isolates_section``
checks that rendering one section's merge prompt does not leak content from
another section. This is the load-bearing architectural invariant of the
project (see ADR-0008). Do not weaken this test.
"""
# ruff: noqa: E501
# Justification: the golden-string snapshots below are byte-stable copies of
# the rendered template output. Reflowing them would either invalidate the
# snapshot or require breaking the test contract. Snapshots stay verbatim.

from __future__ import annotations

import pytest
from jinja2 import UndefinedError

from remory.schema import Schema, SchemaSection, load_builtin
from remory.sleep.extract import ExtractCandidate
from remory.sleep.prompts import (
    CritiqueContext,
    ExtractContext,
    MergeContext,
    RawForExtract,
    render_critique_prompt,
    render_extract_prompt,
    render_merge_prompt,
    render_merge_revise_prompt,
)
from remory.topic import Knobs

_KNOBS = Knobs(tone="warm", strictness="balanced")


def _job_profile() -> Schema:
    return load_builtin("job-profile")


def _extract_ctx() -> ExtractContext:
    raws = (
        RawForExtract(
            relative_path="raw/2026/2026-05-09-0930.md",
            created_iso="2026-05-09T09:30:00Z",
            body="User said X.",
        ),
    )
    return ExtractContext(schema=_job_profile(), knobs=_KNOBS, raws=raws)


def _skills_section() -> SchemaSection:
    return _job_profile().sections[0]


def _merge_ctx() -> MergeContext:
    return MergeContext(
        section=_skills_section(),
        current_section_text="Existing prose about skills.",
        candidates=(
            ExtractCandidate(
                text="Strong preference for solo deep-focus work",
                evidence="raw/2026/2026-05-09-0930.md",
            ),
        ),
        persona=_job_profile().persona,
        knobs=_KNOBS,
    )


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


_EXPECTED_EXTRACT = """\
You are extracting candidate updates from raw conversation entries for the
topic "job-profile". Persona context (background only, do not adopt it
as your voice here):

You are a thoughtful career coach. You help the user articulate what
they actually want from work, surface contradictions kindly, and
refuse to be flattered into agreement.


Tone knob: warm. Strictness knob: balanced.

The state.md for this topic is divided into the following sections. Each
candidate update you produce MUST be assigned to exactly one of these
section ids:

- skills_and_strengths ("Skills and strengths"): What the user is genuinely good at and what they enjoy doing.
- values_and_priorities ("Values and priorities"): What matters most to them about work and life balance.
- hard_constraints ("Hard constraints"): Non-negotiables. Geography, salary floor, family obligations.
- options_considered ("Options considered"): Specific roles or directions evaluated, with pros and cons.
- current_leaning ("Current leaning"): Where the user is currently pointing, with confidence level.
- evidence_log ("Evidence log"): Append-only log of dated insights and where they came from.

Below are the raw entries to process. Each raw entry has a path under
``raw/<year>/<file>.md``; cite that path verbatim as the ``evidence`` for
any candidate you derive from it.

=== raw/2026/2026-05-09-0930.md (created 2026-05-09T09:30:00Z) ===

User said X.


Respond with a JSON object mapping each section id to a list of candidate
updates. Each candidate has the shape:

  {"text": "<one-line statement>", "evidence": "raw/<year>/<file>.md"}

Sections with no candidate updates can be omitted or have an empty list.
Do not include any section ids not listed above.
"""


_EXPECTED_MERGE = """\
You are updating one section of state.md. You can see ONLY this section.
Other sections are intentionally hidden from you so your update cannot
drift into them.

Persona (adopt this voice):

You are a thoughtful career coach. You help the user articulate what
they actually want from work, surface contradictions kindly, and
refuse to be flattered into agreement.


Tone: warm. Strictness: balanced.

Section: "Skills and strengths" (id: skills_and_strengths).
Section description: What the user is genuinely good at and what they enjoy doing.

Current section text (may be empty):

<<<CURRENT
Existing prose about skills.
CURRENT>>>

Candidate updates extracted from recent raw entries, with evidence paths:

- Strong preference for solo deep-focus work  (evidence: raw/2026/2026-05-09-0930.md)

Rewrite the section. Integrate the candidate updates into the prose. Keep
existing material that remains accurate. Drop or correct material that the
new evidence contradicts. Address the user in second person. Do NOT include
the section heading -- output only the body of this section, ending with a
single newline.
"""


_EXPECTED_MERGE_REVISE = """\
You just produced a draft for the section "Skills and strengths". Now revise
it. Check for:

- consistency with the requested tone (warm) and strictness
  (balanced)
- claims you cannot support from the candidate evidence
- prose that drifts into other sections (you saw only this section's
  description: What the user is genuinely good at and what they enjoy doing.)
- redundancy with the original section text

Original section text:

<<<CURRENT
Existing prose about skills.
CURRENT>>>

Your draft:

<<<DRAFT
draft body
DRAFT>>>

Output a revised body for this section only. No heading. End with a single
newline.
"""


_EXPECTED_CRITIQUE = """\
You are reviewing the freshly consolidated state.md for the topic
"job-profile". Persona (background only, do not adopt as voice):

You are a thoughtful career coach. You help the user articulate what
they actually want from work, surface contradictions kindly, and
refuse to be flattered into agreement.


Tone: warm. Strictness: balanced.

Full state.md text (frontmatter and all sections):

<<<STATE
---
schema: job-profile
---

# A

body.

STATE>>>

Produce a markdown review note covering:

1. Possible contradictions across sections.
2. Sections that look thin, stale, or under-supported relative to others.
3. Claims that do not appear to trace back to the evidence log.

Be concrete. Quote section titles. Do not modify state.md -- this output is
written to ``_review.md`` for the user to read on their own time.
"""


def test_extract_prompt_snapshot_matches_golden() -> None:
    rendered = render_extract_prompt(_extract_ctx(), stricter=False)
    assert rendered == _EXPECTED_EXTRACT


def test_merge_prompt_snapshot_matches_golden() -> None:
    rendered = render_merge_prompt(_merge_ctx())
    assert rendered == _EXPECTED_MERGE


def test_merge_revise_prompt_snapshot_matches_golden() -> None:
    rendered = render_merge_revise_prompt(_merge_ctx(), draft="draft body")
    assert rendered == _EXPECTED_MERGE_REVISE


def test_critique_prompt_snapshot_matches_golden() -> None:
    state_text = "---\nschema: job-profile\n---\n\n# A\n\nbody.\n"
    ctx = CritiqueContext(schema=_job_profile(), knobs=_KNOBS, state_md_text=state_text)
    rendered = render_critique_prompt(ctx)
    assert rendered == _EXPECTED_CRITIQUE


def test_render_extract_prompt_stricter_differs_measurably() -> None:
    base = render_extract_prompt(_extract_ctx(), stricter=False)
    stricter = render_extract_prompt(_extract_ctx(), stricter=True)
    assert base != stricter
    assert "ONLY a JSON object" in stricter
    assert "ONLY a JSON object" not in base


def test_render_merge_prompt_isolates_section() -> None:
    """Section-isolation regression test (see ADR-0008).

    Rendering one section's merge prompt must not contain the title or
    current-text of any other section in the same schema. The prompt sees
    only what the :class:`MergeContext` carries; this asserts the
    architectural invariant at the seam.
    """
    schema = _job_profile()
    skills = next(s for s in schema.sections if s.id == "skills_and_strengths")
    constraints = next(s for s in schema.sections if s.id == "hard_constraints")

    candidate = ExtractCandidate(text="text", evidence="raw/2026/2026-05-09-0930.md")

    mc_skills = MergeContext(
        section=skills,
        current_section_text="SKILLS_BODY_MARKER",
        candidates=(candidate,),
        persona=schema.persona,
        knobs=_KNOBS,
    )
    mc_constraints = MergeContext(
        section=constraints,
        current_section_text="CONSTRAINTS_BODY_MARKER",
        candidates=(candidate,),
        persona=schema.persona,
        knobs=_KNOBS,
    )
    skills_rendered = render_merge_prompt(mc_skills)
    constraints_rendered = render_merge_prompt(mc_constraints)

    assert "SKILLS_BODY_MARKER" in skills_rendered
    assert "Skills and strengths" in skills_rendered
    # Other section's title and body must NOT appear in the skills prompt.
    assert "CONSTRAINTS_BODY_MARKER" not in skills_rendered
    assert "Hard constraints" not in skills_rendered

    assert "CONSTRAINTS_BODY_MARKER" in constraints_rendered
    assert "Hard constraints" in constraints_rendered
    assert "SKILLS_BODY_MARKER" not in constraints_rendered
    assert "Skills and strengths" not in constraints_rendered


def test_strict_undefined_raises_on_missing_template_key() -> None:
    """A missing field on a context dataclass must surface, not silently render empty.

    StrictUndefined is the bedrock of the prompts module's "fail fast on
    typos" promise. We exercise it by importing the env directly and
    rendering a minimal template that references a name not in the
    environment.
    """
    from remory.sleep import prompts as prompts_module

    template = prompts_module._env.from_string("{{ does_not_exist }}")
    with pytest.raises(UndefinedError):
        template.render()
