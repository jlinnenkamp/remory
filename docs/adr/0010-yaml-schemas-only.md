# ADR 0010: Topic schemas are declarative YAML, not Python plugins

**Status:** Accepted. Foundational decision from build spec §2.

## Context

A topic type — `job-profile`, `workout`, `coaching`, or a user's own —
is defined by a schema that names the sections of `state.md`, the
persona Claude adopts during chat, the sleep depth, the per-topic
knobs, and the questions the wizard asks during setup. The question is
in what form this schema is expressed.

This ADR records the reasoning behind a decision that was settled in
`INSTRUCTIONS.md` §2 and §5 rather than deliberated in a PR. The
schema format is locked; the Alternatives section below does the real
work of explaining why the rejected paths are worse.

The decision: a schema is a YAML file. Built-in schemas live in
`src/remory/schemas_builtin/` and are validated against a Pydantic
model at package import. User-authored schemas live in
`$XDG_CONFIG_HOME/remory/schemas/` and are validated on first use.
Both load through the same Pydantic model. Built-in names are
reserved: a topic configured against a built-in name always loads the
bundled schema, and user schemas exist for net-new topic types only.

## Decision

`schema.py` exposes a `Schema` Pydantic model and two loaders:
`load_builtin(name)` reads from the bundled package data,
`load_user(path)` reads from the user config directory. The topic
loader picks between them by name: a topic whose schema name matches
one of the reserved built-in names always loads the bundled schema;
any other name is resolved from `$XDG_CONFIG_HOME/remory/schemas/`.
The Pydantic model is the single validation point: shape errors, type
errors, unknown fields (`extra="forbid"` for built-ins;
`extra="ignore"` for user schemas, for forward-compat tolerance), and
cross-field constraints (e.g. `append_only` sections must not have
`wizard_questions` referencing them) all surface as Pydantic
`ValidationError` instances, which the CLI surface formats into a
human-readable error pointing at the offending file and line.

A user adds a topic type by dropping a single YAML file into the
config directory. They do not install a Python package, do not write a
`setup.py` entry point, do not register anything. `remory topics` and
`remory init` both pick the file up on next run.

## Consequences

YAML's quoting and indentation quirks become a support surface. The
Pydantic error path is therefore part of the user-facing UX, not an
internal-only failure mode. Error messages must point at the schema
file path and, where the parser can recover line numbers, the offending
line. A schema validation failure during `remory init` or `remory
doctor` must not leak a Pydantic traceback to the terminal; it should
render as a single readable diagnostic.

Built-in names being reserved means a user who wants to customise a
built-in topic's behaviour cannot do it by dropping a same-named YAML
into the config directory. The v0.1 levers are the per-topic `knobs:`
in `meta.yaml` (which the wizard sets and the user can edit) and
forking the schema under a different name in the user config directory.
The reserved-name rule trades the convenience of in-place override for
the predictability of "the bundled `workout` is always the bundled
`workout`" — a Remory upgrade that changes the built-in workout schema
applies cleanly to every user's workout topic without surprise.

User-authored schemas are shareable as single files. A user can paste
a friend's schema into their config directory and it works. A user
can publish a schema as a Gist or in a README and it is one `curl |
tee` away from being installed. This is a property worth preserving.

## Alternatives considered

- **Python entry-points (each topic type ships as a `pip`-installable
  package).** Rejected. It raises the floor for contributing a topic
  type from "write a YAML file" to "package and publish a Python
  module," drags in plugin-discovery complexity (entry-point scanning,
  version pinning, environment isolation), and makes schemas hostile
  to share. A YAML file can be pasted into a chat or a Gist; a
  `pip`-installable package cannot. The audience for custom topics is
  users who want to model their own life, not Python package authors.
- **JSON instead of YAML.** Rejected. JSON has no comment syntax. The
  schema file is a file the user is expected to read, edit, and reason
  about — the `description:` field of a section is documentation the
  next user (or the same user six months later) needs to understand
  what the section is for. A format without comments is the wrong
  choice for a file with a documentation surface.
- **TOML instead of YAML.** Viable, and Python's first-party TOML
  support is real. Rejected on fit. The schema's shape is nested-list
  heavy: a list of sections, each section a record with several
  fields, sometimes containing further lists (`wizard_questions` and
  their `options`). TOML's array-of-tables syntax for this is verbose
  and visually awkward compared to YAML's indented list-of-mappings.
  The schema's `persona:` and `description:` fields are also
  multi-line prose, which YAML's block scalars handle gracefully and
  TOML handles with backslash-continued strings.

## References

- `INSTRUCTIONS.md` §2 (the "Schemas" and "Schema customisation" rows
  of the locked decisions table), §5 (the full schema spec, including
  the `job-profile` worked example and the built-in `workout` and
  `coaching` specifics), §10 (the per-topic `CLAUDE.md` generation
  that consumes the schema's persona and knobs).
