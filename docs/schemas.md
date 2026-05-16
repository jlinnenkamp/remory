# Schemas

This document is the reference for Remory's topic schemas. A schema defines what a topic type looks like: its sections, the persona Claude adopts during chat, the sleep depth, the per-topic knobs, and the questions the wizard asks during `remory init`. If you are authoring a custom schema, read this top to bottom. For the prose-level explanation of how schemas fit into the runtime, see [`architecture.md`](./architecture.md).

## Overview

A schema is a single YAML file. Built-in schemas ship inside Remory at `src/remory/schemas_builtin/`; user-authored schemas live in `$XDG_CONFIG_HOME/remory/schemas/`. Both load through the same Pydantic model. The schema format choice — YAML, declarative, single-file, no Python plugin layer — is recorded in [ADR-0010](./adr/0010-yaml-schemas-only.md).

## File location

A schema's filename is `<name>.yaml`, where `<name>` matches the `name:` field inside the file. That is how a user invokes commands against the topic type: `remory chat <name>`, `remory sleep <name>`. The built-ins (`job-profile`, `workout`, `coaching`) ship with the package and are loaded from `importlib.resources` at runtime — they are never copied to disk.

User schemas live in `$XDG_CONFIG_HOME/remory/schemas/<name>.yaml`. When a topic refers to a schema that is not one of the built-in names, the loader reads it from that directory. The built-in names are reserved: a topic configured against a built-in name always loads the bundled schema; the loader does not look in the user config directory for a name that collides with a built-in. If you want to customise the workout topic's behaviour, the v0.1 levers are the per-topic `knobs:` in `meta.yaml` (which the wizard sets and which you can edit) and forking the schema under a different name in your config directory.

The convention that filename matches the `name:` field is convention, not enforcement; the loader keys on the `name:` field, not the filename. Matching them keeps the config directory readable.

## Top-level fields

The four scalar top-level fields are `name`, `version`, `description`, and `persona`. All four are required.

**`name`** is the topic-type identifier. It is what the user passes to `remory chat <name>`, `remory sleep <name>`, and every other command that takes a topic argument. Use kebab-case (`job-profile`, not `Job Profile` or `job_profile`); the built-ins set the convention. The schema validator enforces a `^[a-z][a-z0-9_-]*$` pattern.

**`version`** is an integer. The only legal value in v0.1 is `1`. The field exists to allow forward-compatible changes to the schema format itself (a future Remory version that wants to add a new top-level field would bump the format version and migrate older schemas on load). It is not the schema-content version — adding or renaming sections inside a schema does not bump this field. See "Versioning and migration" below.

**`description`** is human-readable prose explaining what this topic type is for. It appears in `remory topics` output and in `remory init`'s topic-picker. Use a YAML block scalar (`description: |`) for anything longer than a sentence. The audience is the user, not the model.

**`persona`** is the voice Claude adopts during _chat sessions_ for this topic. It is interpolated into the per-topic `CLAUDE.md` that Remory writes at topic creation. Important: this is _not_ a system prompt for the whole pipeline. The sleep-pipeline prompts are bundled with Remory and not user-overridable — see [ADR-0011](./adr/0011-bundled-prompts.md). The `persona:` field only flavours interactive chat. Keep it a few sentences; longer personas dilute. The built-in `workout.yaml` persona is a good length reference.

## Sections

The `sections:` field is a list of section definitions, in the order they will appear in `state.md`. Each section has four fields: three required (`id`, `title`, `description`) and one optional (`append_only`).

**`id`** is the section's stable identifier. The extractor uses it as a JSON key when routing candidate updates; the merger looks the section up by id; `state.md`'s rendered structure is by `title`, but the wiring under the hood is by id. Use `snake_case`. Renaming an id is a breaking change: the rendered `state.md` headings change, and an older `meta.yaml` referring to the old id is now stale. Adding a new section is non-breaking — sleep creates it on the next run with empty content.

**`title`** is the human-readable heading that appears in `state.md`. It is what the user reads. It can be changed without breaking anything as long as the `id` stays the same.

**`description`** is documentation for the section, intended for both the model (it appears in the merger's prompt as context for what the section is supposed to contain) and the next human to read the schema file. Be specific. "What the user is genuinely good at and what they enjoy doing" tells the merger what belongs in `skills_and_strengths`; "Skills" does not.

**`append_only`** defaults to `false`. When set to `true`, the merger is bypassed for that section: raw text is appended verbatim with a date stamp, no LLM rewrite. Use this for evidence-log style sections where the point is uncritical accumulation of dated entries — the `evidence_log` in the built-in `job-profile.yaml` is the canonical example. Setting `append_only: true` on a section the model is expected to rewrite is a footgun (see "Common pitfalls" below).

Section order in the YAML is the rendered order in `state.md`. Reordering sections in the schema reorders them in the rendered output on the next sleep.

## Sleep configuration

The `sleep:` block has two fields, both required.

**`default_depth`** is either `single_pass` or `merge_and_critique`. `single_pass` runs only extract + merge; the critic stage is skipped, and no `_review.md` is written. `merge_and_critique` adds the critic stage on top — it reads the full updated `state.md` and writes its observations to `_review.md`, which Remory prints the path to at the end of sleep. Choose `single_pass` for topics where cross-section contradictions are unlikely or low-value (a workout plan); choose `merge_and_critique` for topics where they matter (a job search, a coaching arc). The full sleep flow is described in the [Sleep pipeline stages section of `architecture.md`](./architecture.md#sleep-pipeline-stages).

**`trigger_threshold`** is an integer. After a chat session ends, if the topic's `pending_count` in `meta.yaml` is at least this number, Remory prints a single friendly line suggesting `remory sleep`. The line appears once and is never modal — nothing nags. A value of `3` is a reasonable default; the built-ins all use `3`.

## Knob defaults

The `defaults:` block sets the initial values for the per-topic `knobs:` (the user can later edit them in `meta.yaml`, and the wizard sets them during `remory init` if `wizard_questions` are present). Two knobs ship in v0.1.

**`tone`** is one of `warm`, `balanced`, or `direct`. It changes how the bundled prompts frame their output: `warm` softens edges and leads with empathy; `direct` is plainspoken and skips the cushioning; `balanced` is the midpoint.

**`strictness`** is one of `gentle`, `balanced`, or `rigorous`. It changes how hard the model presses on the user's stated positions: `gentle` is encouraging and slow to push back; `rigorous` stress-tests claims and surfaces contradictions readily; `balanced` is the midpoint.

The knob values are wire-format contracts — they are written to `meta.yaml` on disk — so renaming or removing one is a migration, not a free change. See [ADR-0011](./adr/0011-bundled-prompts.md) for the reasoning behind keeping behavioural variation inside this enum-driven surface rather than allowing free-form prompt edits.

## Wizard questions (optional)

The `wizard_questions:` block is a list of questions the wizard asks during `remory init` to set the knobs for this topic. Each question has three fields: `id`, `question`, and `options`.

**`id`** must match a knob name (`tone` or `strictness` in v0.1). The wizard's answer is written to the corresponding `knobs:` field in `meta.yaml`.

**`question`** is the prose question the wizard reads aloud (well, types) to the user. Use a YAML block scalar for multi-line questions. The voice is the wizard subagent's voice — warm, a little playful, one question at a time.

**`options`** is a list of `{ value, label }` mappings. `value` is the knob value that gets written if the user picks this option (must be a legal value for the named knob). `label` is what the user sees. Skipping a question is allowed; the schema's `defaults:` provide the fallback.

A wizard question for a knob that does not appear in `defaults:` is a validation error (see "Common pitfalls" below).

## Validation

Schemas are validated against a Pydantic model in `src/remory/schema.py`. Built-in schemas are validated with strict unknown-key rejection (`extra="forbid"`), so typos in field names surface loudly rather than being silently ignored. User-authored schemas are validated with `extra="ignore"` for forward-compat tolerance across `schema_version` bumps — a user schema written against a newer Remory release will still load on an older one, dropping the unknown fields silently.

Built-in schemas are validated at package import time, so a malformed built-in is caught before any user code runs. User schemas are validated on first use — when a command targets a topic configured against the schema. Validation failures render as a single readable diagnostic pointing at the schema file path or built-in name; the CLI does not leak a Pydantic traceback to the terminal. The format and ADR are [ADR-0010](./adr/0010-yaml-schemas-only.md).

## Versioning and migration

The schema-format `version:` field is the wire-format version of the schema YAML itself, not of the content inside it. v0.1 ships with `version: 1` and that is the only legal value. The field exists so a future Remory release that needs to change the schema format — adding a required top-level field, renaming an existing one, changing the shape of `wizard_questions.options` — can detect older schemas on load and migrate them.

Inside a schema, changes are either breaking or non-breaking with respect to the topic's existing on-disk state:

- **Non-breaking.** Adding a new section. Changing a section's `title` or `description`. Adding a new `wizard_question`. Changing `defaults` for a knob (existing topics keep their `meta.yaml` values; new topics get the new defaults). Changing `default_depth` or `trigger_threshold` (takes effect on the next sleep).
- **Breaking.** Renaming an existing section's `id`. Removing a section. Changing a section's `append_only` flag from `false` to `true` (the merger will refuse to touch the section, and existing prose accumulates uncritically thereafter) or from `true` to `false` (the merger now sees an append-log as its starting state). Renaming a knob or removing a knob value.

A breaking change in a built-in schema is a Remory release-notes item with a migration plan; a breaking change in a user schema is the user's own problem, but `remory doctor` will at least surface the mismatch between the schema and the existing `meta.yaml` / `state.md`.

## A complete example

The built-in `workout.yaml` is reproduced verbatim below, broken into annotated blocks. The annotations are HTML comments between blocks — the YAML content itself is unmodified.

<!--
Block 1: top-level identification and description.

`name` is what the user types: `remory chat workout`. `version: 1` is the
only legal schema-format version in v0.1. The description is a block
scalar (`|`) because it wraps across two lines and appears in `remory
topics` and the `remory init` topic-picker.
-->

```yaml
name: workout
version: 1
description: |
  A living workout plan plus session logs. The plan adapts as you tell me
  what you actually did, and the progressions update from there.
```

<!--
Block 2: persona.

This is the voice Claude adopts during `remory chat workout`. Notice it
is short -- four short sentences -- and concrete about behaviour ("does
not flatter sessions that did not happen, and does not nag"). It is
*not* a system prompt for the sleep pipeline; sleep uses bundled
prompts. This field only flavours interactive chat.
-->

```yaml
persona: |
  You are a calm, no-nonsense training partner. You help the user keep
  a workout plan that actually fits their week, log what they did
  honestly, and progress sensibly. You do not flatter sessions that
  did not happen, and you do not nag.
```

<!--
Block 3: sections.

Five sections, in the order they will appear in `state.md`. None of
them is `append_only` in this schema -- every section is merged by the
LLM on sleep. (Compare with `job-profile.yaml`, where the
`evidence_log` section sets `append_only: true`.) The `description`
field is part of the merger's prompt: it tells the model what content
belongs in this section.
-->

```yaml
sections:
  - id: current_plan
    title: Current plan
    description: The active training plan -- structure, days, key lifts and movements.
  - id: recent_sessions
    title: Recent sessions
    description: A short log of the most recent sessions; older entries roll off.
  - id: progressions
    title: Progressions
    description: How loads, reps, and movements are progressing over time.
  - id: notes_and_injuries
    title: Notes and injuries
    description: Niggles, injuries, recovery context, sleep, and other constraints.
  - id: goals
    title: Goals
    description: What the user is training toward over the next few months.
```

<!--
Block 4: sleep configuration.

`single_pass` skips the critic stage. The workout topic is mostly
self-contained per section (a session log entry rarely contradicts the
goals section across time), so the critic's cross-section pass is low
value here. `trigger_threshold: 3` means after three pending raw
entries, `remory chat workout` will print a friendly suggestion to
sleep on exit.
-->

```yaml
sleep:
  default_depth: single_pass
  trigger_threshold: 3
```

<!--
Block 5: defaults.

The workout topic defaults to `direct` tone (no cushioning) and
`balanced` strictness. A user who wants warmth or rigour overrides via
the wizard questions below, or by editing `meta.yaml` later.
-->

```yaml
defaults:
  tone: direct
  strictness: balanced
```

<!--
Block 6: wizard questions.

Two questions, one per knob. Each `id` matches a knob name in
`defaults:` above (the validator enforces this). The `question` field
is the actual prose the wizard reads to the user; `options` are the
choices presented, with `value` being what gets written to `knobs:`
in `meta.yaml` and `label` being what the user sees.

Note the labels are written in the user's voice ("Warm; meet me where
I am", "Direct; just tell me") rather than as abstract descriptors.
This is the wizard tone: warm, a little playful, one choice at a time.
-->

```yaml
wizard_questions:
  - id: tone
    question: |
      When a session goes badly, do you want me warm about it, or do you
      want me to just say what I see?
    options:
      - { value: warm, label: "Warm; meet me where I am" }
      - { value: direct, label: "Direct; just tell me" }
  - id: strictness
    question: |
      How strict should I be about programming and progression?
    options:
      - { value: gentle, label: "Lenient; life happens" }
      - { value: rigorous, label: "Hold me to the plan" }
```

## Common pitfalls

- **`append_only: true` on a section the model is expected to rewrite.** The merger will refuse to touch the section, and raw text will accumulate verbatim with date stamps. The first sleep where the user reports something contradictory will surface the problem: the contradictory text is just sitting in the section, uncritically. Use `append_only` only for sections whose value is the accumulation, not the synthesis.
- **Missing `value` field on a wizard option.** Pydantic refuses the schema with an error pointing at the line. `value` is the field that gets written to `meta.yaml`; without it, the wizard has nothing to record.
- **Schema `name:` field not matching the filename.** The loader keys on the `name:` field, not the filename, so things still work — but anyone reading your config directory by hand will be confused. Match them.
- **A wizard question for a knob that does not exist in `defaults:`.** Validation refuses. The wizard's answer has nowhere to go if the knob is not declared. Either add the knob to `defaults:` (and make sure Remory's bundled prompts actually read it — adding a new knob name is a code change, not just a YAML change) or remove the wizard question.
