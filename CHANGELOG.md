# Changelog

All notable changes to Remory will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-05-16

First public release. Local-first, terminal-based personal-assistant
harness on top of Claude Code.

### Added

#### CLI commands

- `remory init <topic> --schema <name>` — create a topic from a
  built-in schema (`job-profile`, `workout`, `coaching`). Refuses to
  overwrite an existing topic.
- `remory init` (no arguments) — interactive first-run wizard, driven
  by a Claude Code subagent. Walks through picking topics, sets
  per-topic tone and strictness knobs, and writes a short
  `about-me.md` at the data directory root. Requires `claude`
  installed and logged in; if either is missing the wizard refuses to
  launch and points at `remory doctor`. SIGINT before the wizard
  begins writing leaves no files behind; SIGINT during the write
  phase finishes the in-flight file and stops, surfacing partial
  state via `remory doctor` per the per-topic-atomic contract.
- `remory init --refresh [--force] [--dry-run]` — re-install bundled
  `.claude/` templates and regenerate per-topic `CLAUDE.md`. Preserves
  user-edited files; writes `.bak` before any overwrite.
- `remory chat <topic>` — start an interactive Claude Code session
  inside a topic, with optional `--continue` to resume the most
  recent session. On session end, the conversation is captured as a
  raw entry and the topic's pending counter ticks up. If the topic
  is in an incomplete state, the command points at `remory doctor`
  rather than risking partial-file overwrite.
- `remory sleep <topic>` — consolidate pending raw entries into
  `state.md`. Writes a timestamped `.bak` before any merge work.
  `--dry-run` shows the proposed `state.md` without writing.
  `--if-due` consolidates only topics whose pending count crosses
  their schema threshold (cron-friendly).
- `remory state <topic>`, `remory recent <topic>`,
  `remory review <topic>` — print a topic's current state, last raw
  entries, and last critique review.
- `remory ingest <topic> <file>` — add a markdown file as a raw
  entry, marked `source: ingested`.
- `remory topics`, `remory stats` — list configured topics and
  cross-topic totals (entries, last sleep, simple streaks).
- `remory doctor` — health check covering data-dir writability,
  config validity, the `claude` binary and its login state,
  per-topic schema and parse health, lock orphans, leftover `.tmp`
  files, missing backups, pending entries that look orphaned, bundled
  `.claude/` template drift, and per-topic `CLAUDE.md` drift.
  `--strict` adds a check for hand-edited `state.md` files whose YAML
  frontmatter would be re-formatted on the next sleep.
  `--probe-real-cli` runs a one-shot round-trip to detect path-
  encoding drift between Remory and `claude` (off by default, costs
  an LLM call). Failures exit with code 1.
- `remory --version` — print the installed Remory version.

#### Topics and schemas

- Three built-in topic types — `job-profile`, `workout`, and
  `coaching` — each with a defined set of state sections, a default
  tone and strictness, and wizard questions for first-run setup.
- File formats for per-topic state: `state.md` (YAML frontmatter
  plus schema-defined section headings), `meta.yaml` (consolidation
  counters and per-topic knobs), and raw entry files under
  `raw/<year>/`.
- User-authored topic schemas in YAML, loaded from
  `$XDG_CONFIG_HOME/remory/schemas/`. Built-in schema names
  (`job-profile`, `workout`, `coaching`) are reserved against user
  override; user schemas exist for net-new topic types.

#### Claude Code orchestration

- SessionEnd hook installed by `remory init`: captures transcripts
  as raw entries when you talk to `claude` directly outside
  `remory chat`. See ADR-0002.
- PreToolUse hook installed by `remory init`: refuses any attempt to
  edit `state.md` from within `claude` — the only legitimate writer
  is `remory sleep`.

#### Error surface

- Errors across all commands route through a single mapping that
  names the failure in plain language and points at `remory doctor`
  or the `remory.log` file when remediation is non-obvious. Exit
  codes follow a published contract:
  - `0` — success
  - `1` — generic runtime failure
  - `2` — usage error
  - `3` — backend not found (`claude` missing from PATH)
  - `4` — backend auth (`claude` not logged in)
  - `5` — backend other (timeout, invocation error, parse error)
  - `6` — lock busy (another `remory` operation in progress)
  - `7` — sleep pipeline failure
  - `8` — data parse error
  - `9` — config error
  - `99` — uncaught (file a bug)
  - `130` — SIGINT (user cancelled)

### Fixed

- `remory doctor` now exits 1 cleanly on failure rather than 99 with a
  misleading "unexpected error" banner. This affected pre-release
  users who ran doctor against a virgin data directory.

[Unreleased]: https://github.com/jlinnenkamp/remory/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jlinnenkamp/remory/releases/tag/v0.1.0
