# ADR 0011: Prompt templates are bundled with Remory; users tune knobs, not prompts

**Status:** Accepted (foundational).
**Date:** 2026-05-16.

## Context

The sleep pipeline (§7) runs three stages — extract, merge, critique —
each driven by an LLM prompt. The wizard (§11) runs another. The
subagent system prompts in `.claude/agents/` are themselves templates.
The question is who owns these prompts: Remory, the user, or both.

This ADR records the reasoning behind a foundational design decision;
the prompt-ownership boundary is locked at the project level, not
deliberated per-PR. The Alternatives section below does the real work
of explaining why the rejected paths are worse.

The decision: prompt templates are bundled with Remory and not
user-overridable. They live in `src/remory/sleep/prompts.py` (and in
the bundled `.claude/agents/*.md` templates the `init` command writes
into the user's data directory). Users adjust behaviour through the
per-schema `tone` (warm / balanced / direct) and `strictness` (gentle
/ balanced / rigorous) knobs, which the templates interpolate. The
contract is: same Remory version installed, same prompts running, same
behavioural envelope across users.

## Decision

`sleep/prompts.py` is the only authoritative source for sleep-pipeline
prompts. The bundled `.claude/agents/*.md` templates that `remory
init` materialises into `<data_dir>/.claude/agents/` are sourced from
the package; `remory init --refresh` rewrites them from the package
copy. A user editing them by hand is editing files Remory will
overwrite on the next refresh, and `remory doctor` notes drift.

The knobs system is the user's lever. Tone and strictness are stored
per topic in `meta.yaml` under `knobs:`, set during `remory init` from
the schema's `wizard_questions`, and interpolated into the bundled
templates wherever those knobs are referenced. Adding a new
behavioural dimension means extending the knob enum and threading it
through the templates — a code change with tests, not a config knob.

Snapshot tests on rendered prompts (`test_prompts_snapshot.py`, §12)
catch accidental regressions in PR review. A prompt change is visible
in the diff.

## Consequences

A user who needs different prompts forks the repo. The cost of
accepting prompt forks downstream is much lower than the cost of
accepting prompt edits in-place: a fork is honest about being a
different product; an in-place edit is invisible from the outside,
breaks reproducibility, and turns every support request into "what
does your prompt look like."

The behavioural surface the project commits to is the cartesian
product of tone × strictness × schema. That is the contract for
behavioural variation. Widening it requires extending the knobs
surface — adding a new enum value, adding a new knob dimension,
adding a new schema section — through code, not through user-editable
files. This keeps the surface auditable and testable.

If the knob enum ever needs to change — a new tone value added, a
deprecated value renamed, a value removed — it is a wire-format
contract written to `meta.yaml` on disk, and a migration plan applies.
This is the same forward-compatibility discipline that governs other
on-disk enums in the project.

## Alternatives considered

- **A `prompts/` directory under `$XDG_CONFIG_HOME/remory/` that users
  can edit.** The "obvious" customisation surface. Rejected. Users
  break their own pipeline silently and the breakage is invisible to
  the maintainer. A user reporting "sleep keeps producing bad merges"
  is unreproducible if their local prompt has drifted from the bundled
  one, and the support flow becomes "send me your prompts" before any
  real diagnosis can start. The bundled-only rule trades a customisation
  knob we do not need for a reproducibility property we cannot get any
  other way.
- **Per-topic prompt overrides in the schema YAML.** Rejected for the
  same reason as the previous alternative, with a worse failure mode:
  every schema becomes a potential prompt fork, and a user sharing a
  schema with a friend is now also sharing prompt edits, which may not
  compose with the friend's Remory version.
- **A `--prompt-template <path>` flag on `remory sleep`.** Rejected. A
  one-shot override is the worst version of this: the user can run a
  sleep against a custom prompt, then run the next sleep without it,
  and the resulting `state.md` is a Frankenstein of two prompt
  surfaces. The mode is also a debugging footgun — every bug report
  has to start with "did you pass `--prompt-template`?"

## References

- `docs/architecture.md` "Sleep pipeline stages" — the three stages
  whose prompts this ADR governs.
- `docs/architecture.md` "What v0.1 excludes" — names the
  no-prompt-overrides rule this ADR formalises.
- `tests/unit/test_prompts_snapshot.py` — snapshot tests on rendered
  prompts; a prompt change is visible in the diff in PR review.
