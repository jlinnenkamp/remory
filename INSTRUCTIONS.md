# Remory — Build Specification

> A second brain that actually remembers.

This document is the source of truth for building **Remory**, a terminal-based personal-assistant harness on top of Claude Code. Read it end-to-end before writing code. The companion file `CLAUDE.md` defines the behaviour expected of Claude Code itself when working inside this repository.

If anything below is ambiguous or contradicts other guidance, **stop and ask the human**. Do not improvise architecture decisions; they have all been deliberated.

---

## 1. What Remory is

Remory is a CLI that lets a user have **persistent, topic-scoped conversations** with Claude. Each topic accumulates raw conversation transcripts, and a deliberate "sleep" cycle distils those raw entries into a structured `state.md` that becomes the canonical memory for that topic.

The product north star is: **easy and fun to use, feels like the second brain understands you, gently addictive in the way a beloved book is**. Tone is warm. Defaults are calm. Nothing nags.

### The three built-in topic types (MVP)

1. **`job-profile`** — interviews, self-reflection, and notes from people the user has spoken with about their career direction. Output: an evolving picture of the optimal job profile.
2. **`workout`** — a living workout plan plus session logs. The plan adapts as the user reports what they actually did.
3. **`coaching`** — Q&A from therapy or coaching sessions. Insights accumulate across themes over time.

Custom topic types are supported from day one via user-authored YAML schemas.

### The recency-bias problem this product solves

Naive LLM memory systems weight recent conversations too heavily and forget older context. Remory's architectural answer is **section-isolated consolidation**: each section of `state.md` is updated by a dedicated LLM call that sees only that section's current content plus the candidate updates relevant to it. The LLM literally cannot drift into other sections because they are not in its context window.

---

## 2. Top-level decisions (locked)

These are settled. Do not relitigate.

| Decision | Choice |
|---|---|
| Project name | **Remory** |
| CLI binary | `remory` (no aliases) |
| Python version | **3.12+** |
| Package layout | `src/` layout |
| Project tooling | **uv** for environments and dependencies |
| Lint/format | **ruff** |
| Type checker | **pyright** |
| Test framework | **pytest** |
| License | **AGPL-3.0-or-later** |
| Telemetry | **None.** Hard architectural property. Stated explicitly in README. |
| LLM backend (default) | `ClaudeCodeBackend` — subprocess to local `claude` CLI; uses Anthropic Max subscription |
| LLM backend (stub) | `AnthropicAPIBackend` — off by default, requires `ANTHROPIC_API_KEY`; ships as a reference for contributors |
| Distribution | `pipx install git+https://...` for v0.1; PyPI publish deferred until v0.2 |
| Supported OS | Linux and macOS first-class; Windows on best-effort (paths via `pathlib`) |
| Data directory | `$XDG_DATA_HOME/remory/` (Linux/macOS), resolved via `platformdirs`. **Never inside the repo.** |
| Config directory | `$XDG_CONFIG_HOME/remory/` |
| Logs directory | `$XDG_STATE_HOME/remory/logs/` |
| Schemas | YAML, declarative; built-in three plus user schemas in `$XDG_CONFIG_HOME/remory/schemas/` |
| Schema customisation | Tone (warm ↔ direct) and strictness (gentle ↔ rigorous) per schema, set during `remory init` |
| Prompt templates | Bundled, not user-overridable. Per-schema knobs vary tone/strictness within bundled templates. |
| Raw retention | Forever, organised by year |
| Session model | `remory chat <topic>` starts a fresh Claude session by default; `--continue` resumes via `claude --resume` |
| External ingestion | `remory ingest <topic> <file>` accepted; treated as raw entry tagged `source: ingested` |
| Sleep trigger | Manual via `remory sleep <topic>`. Threshold-based suggestion at session end. `remory sleep --if-due` exists for future cron use. |
| Sleep depth defaults | `workout`: single-pass merge. `job-profile`, `coaching`: merge + critique. Override per-schema in YAML. |
| `_review.md` after sleep | Print path. Do not auto-open. |
| Atomic writes | Required for `state.md`. Temp file + rename. |
| Backups | Timestamped backup of `state.md` before every sleep, kept in `.backups/`. Non-negotiable. |
| File locking | Required to prevent collisions between interactive session and cron-driven sleep. |

---

## 3. Repository layout

```
remory/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE                       # AGPL-3.0-or-later text
├── INSTRUCTIONS.md               # this file (build spec)
├── CLAUDE.md                     # how Claude Code behaves in this repo
├── .gitignore
├── .github/
│   └── workflows/
│       └── ci.yml
├── .claude/
│   ├── agents/
│   │   ├── extractor.md          # subagent: raw → candidate updates
│   │   ├── merger.md             # subagent: per-section merge
│   │   ├── critic.md             # subagent: cross-section critique
│   │   ├── wizard.md             # subagent: first-run setup
│   │   ├── architect.md          # dev-time: proposes module layouts
│   │   ├── implementer.md        # dev-time: writes code per spec
│   │   └── reviewer.md           # dev-time: code-review pass
│   ├── commands/
│   │   ├── sleep.md              # /sleep slash command
│   │   ├── state.md              # /state slash command
│   │   ├── recent.md             # /recent slash command
│   │   └── review.md             # /review slash command
│   └── settings.json             # hooks, allowed tools (see §10)
├── src/
│   └── remory/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py                # Typer app, command dispatch
│       ├── config.py             # Pydantic config + XDG resolution
│       ├── paths.py              # canonical path helpers
│       ├── locking.py            # file lock primitives
│       ├── logging_setup.py
│       ├── topic.py              # Topic, TopicMeta data classes
│       ├── schema.py             # Pydantic schema models, loader, validator
│       ├── state.py              # state.md read/write, atomic, backup
│       ├── raw.py                # raw entry storage, year folders, status
│       ├── transcripts.py        # Claude Code JSONL transcript reader
│       ├── backends/
│       │   ├── __init__.py
│       │   ├── base.py           # Backend ABC
│       │   ├── claude_code.py    # default: subprocess to `claude` CLI
│       │   └── anthropic_api.py  # stub: direct API
│       ├── sleep/
│       │   ├── __init__.py
│       │   ├── orchestrator.py   # extract → merge → critique pipeline
│       │   ├── extract.py
│       │   ├── merge.py
│       │   ├── critique.py
│       │   └── prompts.py        # bundled prompt templates
│       ├── chat.py               # interactive chat session launcher
│       ├── wizard.py             # first-run setup conductor
│       ├── ingest.py             # external file ingestion
│       ├── stats.py              # `remory stats`
│       ├── doctor.py             # `remory doctor`
│       ├── ui.py                 # rich-based output helpers
│       └── schemas_builtin/
│           ├── job-profile.yaml
│           ├── workout.yaml
│           └── coaching.yaml
└── tests/
    ├── conftest.py
    ├── fakes/
    │   └── fake_claude            # fake `claude` binary on PATH for tests
    ├── unit/
    │   ├── test_config.py
    │   ├── test_schema.py
    │   ├── test_state.py
    │   ├── test_raw.py
    │   ├── test_transcripts.py
    │   ├── test_sleep_orchestrator.py
    │   ├── test_locking.py
    │   └── test_prompts_snapshot.py
    └── integration/
        ├── test_chat_flow.py
        ├── test_sleep_flow.py
        └── test_doctor.py
```

---

## 4. The data directory layout (per user, on disk)

```
$XDG_DATA_HOME/remory/
├── about-me.md                   # written by the wizard; meta-context
├── config.toml                   # user-editable preferences
├── topics/
│   ├── job-profile/
│   │   ├── CLAUDE.md             # auto-generated; tells Claude Code how to behave for THIS topic
│   │   ├── state.md              # YAML frontmatter + sectioned markdown body
│   │   ├── meta.yaml             # last_consolidated, pending_count, schema_version, knobs
│   │   ├── raw/
│   │   │   ├── 2026/
│   │   │   │   ├── 2026-05-07-1820.md
│   │   │   │   └── 2026-05-09-0930.md
│   │   ├── _review.md            # critic output (overwritten each sleep)
│   │   └── .backups/
│   │       └── state.md.2026-05-07-1820.bak
│   ├── workout/
│   └── coaching/
└── logs/
    └── remory.log
```

The user's data and the source repo are strictly separate. No user data ever lives in the source tree.

### `state.md` format

```markdown
---
schema: job-profile
schema_version: 1
last_consolidated: 2026-05-07T18:20:00Z
entries_consolidated: 12
---

# Skills and strengths

(prose paragraphs, written in second person addressing the user)

# Values and priorities

...

# Hard constraints

...

# Options considered

...

# Current leaning

...

# Evidence log

- 2026-04-15: Interview with Jane (raw/2026/2026-04-15-1200.md) — surfaced preference for autonomy
- 2026-04-22: Self-reflection — questioned whether team-lead role still appeals
```

Section names come from the schema. Frontmatter is parsed/written via Pydantic. Body is plain markdown.

### `meta.yaml` format

```yaml
schema: job-profile
schema_version: 1
created: 2026-05-01T10:00:00Z
last_consolidated: 2026-05-07T18:20:00Z
last_chat: 2026-05-09T09:30:00Z
pending_count: 2          # raw entries since last consolidation
total_entries: 14
knobs:
  tone: warm              # warm | balanced | direct
  strictness: gentle      # gentle | balanced | rigorous
```

### Raw entry format

Each raw entry is a markdown file with YAML frontmatter:

```markdown
---
created: 2026-05-07T18:20:00Z
source: chat              # chat | ingested | external-transcript
status: pending           # pending | consolidated | archived
session_id: abc123        # Claude Code session id (when source=chat)
duration_seconds: 1840
---

(full conversation transcript, normalised to markdown:
**You:** ...
**Remory:** ...
)
```

---

## 5. Schema spec (YAML)

A topic schema lives at `src/remory/schemas_builtin/<name>.yaml` (built-in) or `$XDG_CONFIG_HOME/remory/schemas/<name>.yaml` (user). Both are loaded identically.

```yaml
name: job-profile
version: 1
description: |
  Builds an evolving picture of your optimal job profile from interviews,
  self-reflection, and conversations with people in your life.

# Persona that Claude adopts during chat for this topic.
persona: |
  You are a thoughtful career coach. You help the user articulate what
  they actually want from work, surface contradictions kindly, and
  refuse to be flattered into agreement.

# Sections of state.md, in order.
sections:
  - id: skills_and_strengths
    title: Skills and strengths
    description: What the user is genuinely good at and what they enjoy doing.
  - id: values_and_priorities
    title: Values and priorities
    description: What matters most to them about work and life balance.
  - id: hard_constraints
    title: Hard constraints
    description: Non-negotiables. Geography, salary floor, family obligations.
  - id: options_considered
    title: Options considered
    description: Specific roles or directions evaluated, with pros and cons.
  - id: current_leaning
    title: Current leaning
    description: Where the user is currently pointing, with confidence level.
  - id: evidence_log
    title: Evidence log
    description: Append-only log of dated insights and where they came from.
    append_only: true

# How aggressive is sleep consolidation.
sleep:
  default_depth: merge_and_critique   # single_pass | merge_and_critique
  trigger_threshold: 3                # suggest sleep after N pending entries

# Defaults for the per-topic knobs (user-overridable in meta.yaml).
defaults:
  tone: warm
  strictness: balanced

# Optional: questions the wizard asks during `remory init` to set knobs.
wizard_questions:
  - id: tone
    question: |
      When you say something contradictory across sessions, do you want me to
      gently flag it, or pretend I didn't notice?
    options:
      - { value: warm, label: "Gently flag, with care" }
      - { value: direct, label: "Just call it out" }
  - id: strictness
    question: |
      How rigorous should I be when assessing a job option you bring up?
    options:
      - { value: gentle, label: "Encouraging" }
      - { value: rigorous, label: "Stress-test it" }
```

The `schema.py` module loads, validates (Pydantic), and exposes `Schema` objects. Built-in schemas are validated at package import time; user schemas at first use.

### Built-in schema specifics

**`workout.yaml`** — sections: `current_plan`, `recent_sessions`, `progressions`, `notes_and_injuries`, `goals`. Default depth: `single_pass`. Default tone: `direct`. Default strictness: `balanced`.

**`coaching.yaml`** — sections: `ongoing_themes`, `insights_by_theme`, `open_questions`, `breakthroughs`, `action_items`. Default depth: `merge_and_critique`. Default tone: `warm`. Default strictness: `gentle`.

---

## 6. CLI command reference

Built with **Typer**. All commands respect `--config <path>`, `--verbose`, `--debug`.

```
remory init                     # first-run wizard; sets up data dir, picks topics, sets knobs
remory chat <topic>              # start interactive Claude Code session in topic dir
remory chat <topic> --continue   # resume previous session
remory ingest <topic> <file>     # add a markdown file as a raw entry
remory sleep <topic>             # consolidate pending raw entries into state.md
remory sleep <topic> --dry-run   # show proposed new state.md, write nothing
remory sleep --if-due            # consolidate only topics over their threshold (cron-friendly)
remory state <topic>             # print state.md to stdout
remory recent <topic> [--n 5]    # list recent raw entries
remory review <topic>            # print _review.md
remory stats                     # cross-topic stats: entries, last sleep, streaks
remory topics                    # list configured topics
remory doctor                    # health check: claude CLI present? authed? schemas valid?
remory --version
remory --help
```

### Behavioural details

**`remory chat <topic>`** must:
1. Acquire a topic-scoped file lock.
2. Verify `state.md` and `CLAUDE.md` exist for the topic; regenerate `CLAUDE.md` from schema + knobs if stale.
3. `cd` into the topic directory and exec `claude` (or use the configured backend).
4. On clean exit, capture the new session transcript via `transcripts.py`, normalise it to markdown, write a new raw file, increment `pending_count`.
5. If `pending_count >= trigger_threshold`, print a friendly suggestion to run `remory sleep`.

**`remory sleep <topic>`** must:
1. Acquire the topic lock.
2. Take a backup of `state.md`.
3. Run the sleep pipeline (§7).
4. Atomically write the new `state.md`.
5. Mark the consolidated raw entries as `status: consolidated`.
6. Update `meta.yaml` (`last_consolidated`, `pending_count := 0`, `entries_consolidated += N`).
7. Print a summary and the path to `_review.md`.

**`remory doctor`** must check, in order:
- `claude` binary on PATH and runnable.
- Authentication (`claude` is logged in).
- Data directory exists and is writable.
- Each topic has a valid schema, parseable `state.md`, and consistent `meta.yaml`.
- No orphaned raw entries (entries marked `pending` whose timestamp predates `last_consolidated`).
- Backups directory not empty for any topic with a populated state.

Each check prints ✓ / ✗ with a remediation hint on failure.

---

## 7. Sleep pipeline

The sleep pipeline runs three stages. Each invokes the LLM backend headlessly. All prompts live in `src/remory/sleep/prompts.py` as Jinja2-templated strings, with the schema and knobs interpolated.

### Stage 1 — Extract (always runs)

**Input:** all pending raw entries for the topic, schema definition.
**Output:** a structured JSON document of candidate updates, keyed by section id:

```json
{
  "skills_and_strengths": [
    {"text": "User reports strong preference for solo deep-focus work", "evidence": "raw/2026/2026-05-07-1820.md"}
  ],
  "values_and_priorities": [],
  "hard_constraints": [
    {"text": "Cannot relocate from current city for at least 2 years", "evidence": "raw/2026/2026-05-09-0930.md"}
  ]
}
```

Validated against a Pydantic model. If parse fails, retry once with stricter "respond ONLY with JSON" instruction; on second failure, abort sleep with a clear error.

### Stage 2 — Merge (one call per section)

For each section that has candidate updates **and** is not `append_only`:

**Input:** current section text from `state.md`, candidate updates *for that section only*, persona, tone, strictness.
**Output:** rewritten section text.

Sections with `append_only: true` (e.g. `evidence_log`) get appended to mechanically, no LLM call.

This is the load-bearing architectural step. The LLM cannot see other sections. This is non-negotiable.

If the schema's `sleep.default_depth` is `merge_and_critique`, run a sub-pass on each merged section: a generate-then-revise where the model is asked to check its own output for the user's stated tone/strictness. If `single_pass`, skip.

### Stage 3 — Critique (only if `default_depth: merge_and_critique`)

**Input:** the full updated `state.md` after all merges, the schema, the knobs.
**Output:** plain markdown written to `_review.md` containing:
- Possible contradictions across sections.
- Sections that look thin or stale.
- Claims that don't appear to trace back to the evidence log.

The critic never modifies `state.md`. It writes only to `_review.md`. The user reads it on their own time.

### Concurrency and safety

- All three stages run inside a single topic lock acquired at sleep start.
- A `state.md.<timestamp>.bak` is written *before* stage 2 attempts any write.
- Atomic write: write to `state.md.tmp`, fsync, rename. Never touch the live file directly.
- Retries on transient backend failures use `tenacity` with exponential backoff. Capped at 3 attempts per LLM call.

---

## 8. Backend abstraction

`Backend` is an ABC in `src/remory/backends/base.py`:

```python
class Backend(Protocol):
    def chat(self, *, cwd: Path, resume: bool = False) -> ChatResult:
        """Launch interactive session. Returns when session ends."""
    def headless(
        self,
        *,
        prompt: str,
        agent: str | None = None,
        cwd: Path | None = None,
        json_output: bool = False,
        timeout_seconds: int = 600,
    ) -> HeadlessResult:
        """Run a single headless invocation. Used by sleep stages."""
    def health_check(self) -> HealthReport:
        """Used by `remory doctor`."""
```

### `ClaudeCodeBackend` (default)

Wraps the `claude` CLI as a subprocess.
- Interactive: `subprocess.run(["claude"], cwd=cwd)` (TTY-attached, blocks until exit).
- Headless: `subprocess.run(["claude", "-p", prompt, "--agent", agent, "--output-format", "json"], ...)` with timeout, capture, retry on transient failures.
- Transcript capture reads from `~/.claude/projects/<encoded-cwd>/*.jsonl` after session ends.

### `AnthropicAPIBackend` (stub, off by default)

Documents the interface, requires `ANTHROPIC_API_KEY`. Calls the Messages API directly. Useful for contributors and for users who prefer metered API. Ships in v0.1 as a reference implementation; not exercised by default test runs.

Backend selection is via config: `[backend] kind = "claude_code"` (default) or `"anthropic_api"`.

---

## 9. Configuration

`$XDG_CONFIG_HOME/remory/config.toml`:

```toml
[backend]
kind = "claude_code"             # or "anthropic_api"

[ui]
emoji = false                    # remory never uses emojis unless this is true
colour = "auto"                  # auto | always | never

[sleep]
auto_suggest_at_session_end = true

[paths]
data_dir = ""                    # empty = use XDG default
```

Env var overrides: `REMORY_DATA_DIR`, `REMORY_CONFIG_FILE`, `REMORY_BACKEND`. Tests use these heavily.

---

## 10. Claude Code orchestration (the production runtime)

The `.claude/` directory at the **data directory root** (regenerated on `remory init`, not the source repo) defines:

### Subagents (`.claude/agents/*.md`)

Each is a markdown file with YAML frontmatter naming the agent and its allowed tools, followed by the system prompt. They are referenced by `claude -p --agent <name>`. The four production subagents:

- **`extractor`** — input: raw entries + schema. Output: structured JSON candidate updates. Allowed tools: `Read` only.
- **`merger`** — input: one section's current text + candidate updates for that section. Output: rewritten section. Allowed tools: none (pure text-in-text-out).
- **`critic`** — input: full updated `state.md`. Output: `_review.md`. Allowed tools: `Read`, `Write` (to `_review.md` only).
- **`wizard`** — input: schema list. Output: configured topics + knobs + the initial `about-me.md`. Allowed tools: `Read`, `Write`.

### Slash commands (`.claude/commands/*.md`)

Available to the user during `remory chat`:
- `/sleep` — kicks off `remory sleep <current_topic>` from inside a chat session.
- `/state` — show `state.md`.
- `/recent` — list last 5 raw entries.
- `/review` — show `_review.md`.

### Hooks (`.claude/settings.json`)

- **`SessionEnd`** hook: invokes a small Python helper that reads the JSONL transcript, normalises it to markdown, writes it as a new raw entry, and bumps `pending_count`. Prints the friendly threshold suggestion if appropriate.
- **`PreToolUse`** hook on `Edit`/`Write`: blocks any attempt to modify `state.md` during chat. `state.md` is read-only outside sleep. This is enforced by hook, not just by prompt instruction.

### Per-topic `CLAUDE.md`

Auto-generated at topic creation and on schema change. Contains:
- The persona from the schema.
- The user's tone and strictness knobs.
- An explicit instruction: "Do not edit `state.md`. It is updated only during sleep."
- A pointer to `state.md` as the canonical context for this topic.

---

## 11. The `remory init` wizard — the fun bit

This is where the product earns its "warm and a little addictive" feel. The wizard runs as a single Claude Code session driven by the `wizard` subagent, with the harness orchestrating turns.

Flow:

1. **Greet by name.** Ask the user's name. Use it sparingly afterwards.
2. **Pick topics.** Show the three built-ins with one-line descriptions. Multi-select.
3. **For each chosen topic, run its `wizard_questions`.** Two or three short, personality-quiz style questions per topic. Map answers to `tone` and `strictness`.
4. **Ask one cross-cutting question:** *"In one sentence — what are you hoping a second brain helps you do?"* Answer goes into `about-me.md`.
5. **Write the letter.** The wizard generates a one-paragraph "letter from your second brain" that explains, in its own words, what it just learned about how the user wants to be talked to. This becomes the first content of `about-me.md`. It's also displayed back to the user.
6. **Tell the user what's next.** A short "Try `remory chat workout` whenever you're ready" line.

The whole flow takes 2–4 minutes. No skips, but every question has a "skip this for now" option that uses the schema default. The wizard never feels like a form.

---

## 12. Testing strategy

### Unit tests
- Pure logic: schema validation, raw-entry parsing, state.md frontmatter round-tripping, lock acquisition, prompt template rendering.
- Sleep orchestration: mocked backend that returns canned JSON/markdown. Verify the pipeline calls extractor once, merger once *per section with updates*, critic only when configured.
- Snapshot tests on rendered prompt templates (`test_prompts_snapshot.py`). Catches accidental prompt regressions in PR review.

### Integration tests
- A **fake `claude` binary** (`tests/fakes/fake_claude`) is a small Python script placed first on `PATH` for the test session. It accepts the same flags real `claude` does and emits canned JSONL transcripts and JSON responses that the test harness controls.
- `test_chat_flow.py`: full chat → exit → raw entry written → meta.yaml updated.
- `test_sleep_flow.py`: pre-seed pending entries, run sleep, assert state.md and _review.md and meta.yaml are all correct, backup exists.
- `test_doctor.py`: every doctor check, both passing and failing.

### CI (`.github/workflows/ci.yml`)
- Matrix: Python 3.12 and 3.13, Ubuntu and macOS.
- Steps: `uv sync` → `ruff check` → `ruff format --check` → `pyright` → `pytest`.
- No real `claude` binary on CI runners; fake binary is used throughout.

---

## 13. Documentation deliverables

### `README.md` skeleton

- One-sentence pitch.
- Animated GIF or asciinema recording of `remory chat workout` followed by `remory sleep workout` (defer recording itself; leave a placeholder).
- Why Remory exists (the recency-bias problem, in two short paragraphs).
- Quickstart: install via pipx, `remory init`, first chat.
- The three built-in topics, one paragraph each.
- **Data and privacy** section: where data lives, what gets sent to Anthropic via Claude Code (the conversation contents, same as if you used Claude Code directly), zero telemetry guarantee, recommended `.gitignore` patterns for users wanting to version their own brain in a private repo.
- Architecture diagram (ASCII): chat flow vs sleep flow.
- "What this is not" section: not a chatbot wrapper, not a vector DB, not a SaaS. Specifically: not Mem.ai, not Rewind, not a Notion plugin.
- Contributing pointer.
- License.

### `CONTRIBUTING.md`
- Dev setup (uv sync, claude code optional for running real e2e).
- The fake-claude testing pattern.
- Schema authoring guide (link to `docs/schemas.md`).
- The "do not break section isolation" rule, prominent.

### `SECURITY.md`
- Vuln disclosure email.
- Threat model summary: local-only, single-user, trusts the `claude` binary it invokes.

### `CHANGELOG.md`
- "Keep a Changelog" format. Start with `## [Unreleased]`.

### `docs/`
- `docs/schemas.md` — how to author a custom schema.
- `docs/architecture.md` — section isolation, backend abstraction, why no telemetry, why no vector DB in v0.1.
- `docs/adr/` — architecture decision records for: section-isolated merges; claude-CLI as default backend; YAML schemas only; bundled prompts; data dir outside repo.

---

## 14. Build order (phased; each phase ends with passing tests)

This is the order Claude Code should follow. **Do not skip ahead.** Each phase produces a working slice.

**Phase 0 — Project skeleton.** Create the directory layout, `pyproject.toml`, `.gitignore`, LICENSE, empty README, CI workflow, ruff/pyright configs. Confirm `uv sync` and `pytest` (with zero tests) both succeed.

**Phase 1 — Core data layer.** `paths.py`, `config.py`, `schema.py` with built-in schema YAML files, `topic.py`, `state.md` read/write with atomic writes and backups, `raw.py`, `locking.py`. Unit tests for all of these. No LLM yet.

**Phase 2 — Backend abstraction + fake claude.** `backends/base.py`, `backends/claude_code.py` (real subprocess wrapper), `backends/anthropic_api.py` (stub). `tests/fakes/fake_claude` Python script. Integration test that drives a fake interactive chat end-to-end.

**Phase 3 — Sleep pipeline.** `sleep/prompts.py`, `sleep/extract.py`, `sleep/merge.py`, `sleep/critique.py`, `sleep/orchestrator.py`. Snapshot tests on prompts. Integration tests that pre-seed raw entries and assert post-sleep state.

**Phase 4 — CLI surface.** `cli.py` Typer app. All commands wired up. `chat`, `sleep`, `state`, `recent`, `review`, `ingest`, `topics`, `stats`, `doctor`, `--version`. End-to-end integration test of `remory chat → remory sleep` against fake claude.

**Phase 5 — Wizard and `init`.** `wizard.py`, `wizard` subagent definition, the `remory init` command. The wizard subagent should be defined as a markdown file in `.claude/agents/`, generated by `init` itself into the user's data dir.

**Phase 6 — Claude Code subagents and hooks.** Generate `.claude/agents/*.md`, `.claude/commands/*.md`, `.claude/settings.json` into the user's data dir at `init` time. SessionEnd hook script. PreToolUse hook protecting `state.md`.

**Phase 7 — Polish.** README with real examples (use the fake claude to make an asciinema recording), CONTRIBUTING, SECURITY, CHANGELOG, ADRs, `docs/` content. Verify `pipx install git+...` works from a clean machine.

Each phase ends with: `ruff check`, `ruff format --check`, `pyright`, `pytest` — all green. Open a PR, even if you're solo. CI must pass.

---

## 15. Things to actively NOT do in v0.1

- No vector database. No embeddings. No semantic recall. (That is v0.3 territory.)
- No web UI, no Telegram, no remote server. Terminal only.
- No multi-user support. Single user, single machine, single config.
- No encryption at rest. Files are markdown the user can read in any editor — that's a feature.
- No prompt overrides per user. Prompts are bundled. Knobs vary tone/strictness.
- No telemetry. Not even opt-in.
- No automated cron. `remory sleep --if-due` is the *seam* for cron, but no cron is wired in v0.1.
- No "smart" auto-consolidation mid-chat. Sleep is deliberate, manual, separate.

If a feature is not in this spec, do not add it. Surface the request to the human.

---

## 16. Definitions and glossary

- **Topic**: a named subject area (e.g. `job-profile`) the user converses about repeatedly.
- **Schema**: the YAML file defining a topic type's sections, persona, sleep behaviour, and wizard questions.
- **State**: the canonical, distilled markdown representation of what is known about a topic. Lives in `state.md`.
- **Raw entry**: a single conversation transcript or ingested file. Lives in `raw/<year>/`.
- **Sleep**: the deliberate consolidation cycle. Extract → merge → optionally critique.
- **Knobs**: per-topic, per-user tone and strictness settings, set by the wizard.
- **Backend**: the LLM driver. `ClaudeCodeBackend` (default) or `AnthropicAPIBackend` (stub).
- **Section isolation**: the architectural property that a section's update only sees that section. Load-bearing.

---

## 17. When in doubt

- If a decision affects user privacy or data integrity: **bias to safety**.
- If a decision is between "fun" and "rigid": **bias to fun within the bounds of this spec**.
- If a decision contradicts something in this file: **stop and ask the human**.

End of spec.
