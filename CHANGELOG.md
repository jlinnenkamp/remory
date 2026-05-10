# Changelog

All notable changes to Remory will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Three built-in topic types — `job-profile`, `workout`, and `coaching` — each
  with a defined set of state sections, a default tone and strictness, and
  wizard questions for first-run setup.
- File formats for per-topic state: `state.md` (YAML frontmatter plus
  schema-defined section headings), `meta.yaml` (consolidation counters and
  per-topic knobs), and raw entry files under `raw/<year>/`. User-authored
  topic schemas in YAML are loaded from the user config directory and shadow
  built-ins of the same name.
- `remory init <topic> --schema <name>` — create a topic directory from a
  built-in schema (`job-profile`, `workout`, `coaching`). Refuses to overwrite
  an existing topic. The interactive first-run wizard ships in a follow-up
  release; until then, `--schema` is required.
- `remory chat <topic>` — start an interactive Claude Code session inside a
  topic, with optional `--continue` to resume the most recent session. On
  session end, the conversation is captured as a raw entry and the topic's
  pending counter ticks up. If the topic is in an incomplete state, the
  command points at `remory doctor` rather than risking partial-file
  overwrite.
- `remory sleep <topic>` — consolidate pending raw entries into `state.md`.
  Writes a timestamped `.bak` before any merge work. `--dry-run` shows the
  proposed `state.md` without writing. `--if-due` consolidates only topics
  whose pending count crosses their schema threshold (cron-friendly).
- `remory state <topic>`, `remory recent <topic>`, `remory review <topic>` —
  print a topic's current state, last raw entries, and last critique review.
- `remory ingest <topic> <file>` — add a markdown file as a raw entry,
  marked `source: ingested`.
- `remory topics`, `remory stats` — list configured topics and cross-topic
  totals (entries, last sleep, simple streaks).
- `remory doctor` — health check covering data-dir writability, config
  validity, the `claude` binary and its login state, per-topic schema and
  parse health, lock orphans, leftover `.tmp` files, missing backups, and
  pending entries that look orphaned. `--strict` adds a check for
  hand-edited `state.md` files whose YAML frontmatter would be re-formatted
  on the next sleep. `--probe-real-cli` runs a one-shot round-trip to detect
  path-encoding drift between Remory and `claude` (off by default, costs an
  LLM call).
- `remory --version` — print the installed Remory version.
- Errors across all commands now route through a single mapping that names
  the failure in plain language and points at `remory doctor` or the
  `remory.log` file when remediation is non-obvious.
