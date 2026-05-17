# Changelog

All notable changes to Remory will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `remory init --reset` wipes user state (`topics/`, `.remory/`,
  `about-me.md`) under the data dir before running init. Intended
  for testing fresh-install flows; the destructive scope is printed
  to stdout. Rejected when combined with `--refresh` (which only
  touches templates, not user data). Also force-refreshes the bundled
  `.claude/` templates so a single `--reset` picks up any new template
  bytes shipped with the current Remory version.
- `remory sleep` now writes per-stage progress lines to stderr so the
  user can see what's happening during the 1-2 minute pipeline run.
  Format: one line per stage boundary — "Extracting candidate updates
  from N entries...", "Merging section: <id>...", "Appending N to
  section: <id>..." for append-only sections with candidates, and
  "Critiquing the new state.md...". Stderr (not stdout) keeps the
  final summary stream clean for piping. The orchestrator's `sleep()`
  function gains a `progress: Callable[[str], None] | None` keyword
  argument; tests pass `None`.

### Fixed

- Wizard's closing line now tells the user to type `/exit` (or press
  Ctrl+D) so the harness can take over and write the topic dir.
  Previously the wizard said "All set — I'll hand you back" and then
  sat there, because the model can't terminate its own interactive
  claude session and the user had no signal to exit.
- Wizard's letter no longer silently polishes the user's spelling
  or grammar. The `wish` field in `answers.json` was already kept
  verbatim; the letter is now under the same contract ("destill"
  stays "destill"). The user's voice is the user's voice.
- `remory doctor`'s template-edited remediation hint now leads with
  the primary action (`remory init --refresh --force`) and presents
  `--dry-run` as the optional preview, rather than dumping both
  flags in parallel.
- `remory init --refresh` no longer claims `stamp older; .bak saved`
  when overwriting a stamped-but-edited file under `--force`. The
  classification was inherited from the apply path's fallback reason
  string and didn't match the dry-run's pre-apply classification.
  Apply rows now just say `.bak saved` (neutral) so they don't
  contradict the dry-run output.

- `remory init` wizard now actually starts. v0.1.0 shipped a bundled
  `wizard.md` template containing the literal string `{{run_dir}}` as a
  placeholder for the per-launch run-directory path; the harness never
  substituted it, so the wizard subagent either sat at an empty prompt
  waiting for user input or improvised a filesystem-wide `find /` for
  its manifest.
- `remory init` wizard's first-turn user message is no longer a 5-line
  technical brief about the run-directory path. The run dir now lives
  at a fixed location inside the data dir
  (`<data_dir>/.remory/wizard-run-current/`) — wiped and re-staged at
  the start of each run — so the wizard.md template can hard-code the
  path. The initial prompt sent to `claude` is now just `"Help me get
  started."`, which reads as the user inviting the wizard rather than
  the user instructing claude. The wizard subagent treats the first
  user turn as a kick-off (not a real question), opens with a warm
  greeting, and asks for the user's name. Two ancillary improvements:
  the run dir is now inside `cwd`, so claude no longer shows the
  "outside the project" permission prompt for each schema read; and
  the run dir's stable location makes it easier for operators to
  inspect after an aborted run. Upgrading from a prior pre-release
  requires `remory init --refresh --force` so the installed
  `<data_dir>/.claude/agents/wizard.md` picks up the new bytes;
  `remory doctor` flags the drift in the meantime.

### Changed

- `remory doctor` now colours each status glyph when stdout is a TTY:
  `OK` in green, `WARN` in yellow, `FAIL` in red. `SKIP` and `INFO`
  stay uncoloured so the eye lands on rows that need attention.
  `NO_COLOR`, `ui.colour = "never"`, and non-TTY output all suppress
  the colour as before.
- `Backend.chat()` protocol gains an `initial_prompt: str | None =
  None` keyword argument. When set, the prompt is appended as the
  trailing positional argument to the underlying chat invocation
  (`claude --agent <agent> "<initial_prompt>"`). `remory chat` passes
  `None` (unchanged behaviour); the wizard launcher is the first and
  only caller in v0.1 that sets it. Custom backend implementations
  must accept the new kwarg.

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
