# Architecture

This document is the prose-level companion to `INSTRUCTIONS.md`. It explains how Remory is shaped, why the load-bearing pieces are shaped the way they are, and where the boundaries between subsystems sit. Read `INSTRUCTIONS.md` first if you have not — this document references its sections rather than re-deriving them. For schema-format details, see [`schemas.md`](./schemas.md).

## North star

Remory is a terminal-based, local-first second brain. The user converses with Claude about topics they return to — a job search, a workout plan, a coaching arc — and a deliberate "sleep" cycle distils those raw conversations into a sectioned `state.md` that becomes the canonical memory for each topic. Nothing leaves the user's machine except the conversation content that goes to Anthropic via Claude Code, the same as if the user had typed into `claude` directly. There is no cloud, no account, no telemetry, no nag. See `INSTRUCTIONS.md` §1 for the north star statement in the spec.

## The two flows

Two flows carry almost all of the runtime behaviour: the chat flow, which produces raw entries, and the sleep flow, which consolidates them into `state.md`. They share a topic lock and never run simultaneously against the same topic.

### Chat flow

```
                      -- chat flow --

  remory chat workout
        |
        v
  acquire topic lock
        |
        v
  exec `claude` in topics/workout/    <-- reads state.md as context;
        |                                 cannot edit it (PreToolUse hook)
   (you converse)
        |
        v
  session exits cleanly
        |
        v
  read JSONL transcript -> write raw/<year>/<timestamp>.md
        |
        v
  bump pending_count; release lock
        |
        v
  if pending_count >= threshold:
      print friendly suggestion to run `remory sleep`
```

The chat surface launches Claude Code as a child process in the topic's working directory. The model sees `state.md` and the per-topic `CLAUDE.md` as context, and a `PreToolUse` hook on `Edit` and `Write` rejects any attempt to modify `state.md` mid-session. `state.md` is read-only outside sleep — this is enforced by hook, not just by prompt instruction, so a misbehaving model still cannot corrupt the canonical state. The persona for the conversation comes from the schema's `persona:` field, interpolated into the topic's `CLAUDE.md` at topic creation.

After the user exits cleanly, the parent process reads the JSONL transcript Claude Code wrote under `~/.claude/projects/<encoded-cwd>/`, normalises it into a markdown raw entry, and writes it under `raw/<year>/`. The parent always writes; a SessionEnd hook (Phase 6) covers the parent-crash window as a safety net, deferring via a non-blocking lock probe when the parent is still holding the lock. The coordination policy is documented in [ADR-0002](./adr/0002-chat-vs-session-end-hook-raw-write-coordination.md). The `pending_count` in `meta.yaml` increments by one, and if it crosses the schema's `trigger_threshold`, the CLI prints a single friendly line suggesting `remory sleep`. The suggestion is never modal — nothing nags.

### Sleep flow

```
                      -- sleep flow --

  remory sleep workout
        |
        v
  acquire topic lock
        |
        v
  backup state.md -> .backups/state.md.<ts>.bak
        |
        v
  extract: claude --agent extractor   <-- reads raw entries + schema;
        |                                 emits candidate updates per section
        |  +--------------------------------+
        v  v                                |
  for each section with updates:            |  <-- one LLM call per section.
      merger sees only THIS section's       |      The model literally cannot
      current text + THIS section's         |      drift between sections;
      candidates -> rewritten text          |      other sections are not in
        |                                   |      its context window.
        +-----------------------------------+
        |
        v
  (optional) critic: reads full new state.md
        |                              -> writes _review.md
        v                                (never modifies state.md)
  atomic write state.md
        |
        v
  mark raw entries consolidated; update meta.yaml
        |
        v
  print summary + path to _review.md; release lock
```

Sleep is the consolidation cycle. It takes a backup of `state.md` first — non-negotiable — and then runs the extract, merge, and (optionally) critique stages described below. The whole pipeline runs inside the same topic lock that `remory chat` would acquire, so the two flows cannot interleave on the same topic. The atomic write of `state.md` is temp-file plus rename plus fsync, with the durability nuance documented in [ADR-0001](./adr/0001-fsync-on-darwin.md).

The load-bearing property of this flow is in the merge stage: one LLM call per section, with no other section's text in context. The next section explains why this is shaped as it is.

## Section isolation

Section isolation is the architectural property Remory is built around. Each section of `state.md` is updated by a dedicated LLM call that sees only that section's current text and the candidate updates relevant to it. Other sections are not in the model's context window. They cannot drift, because the model literally cannot see them. The decision and its rejected alternatives are recorded in [ADR-0008](./adr/0008-section-isolated-merges.md).

The naive shortcut — one LLM call for the whole state, with the prompt instructing the model to "rewrite section X without touching section Y" — fails for two reasons. First, recency bias is a property of the context window's contents, not of instructions about those contents. A model with the entire `state.md` in its context will pull the older sections toward the colour of the newer inputs, no matter how firmly the prompt asks it not to. Second, even if the model behaved, the project would lose falsifiability: a reviewer reading the code cannot prove, by inspecting the call site, that drift did not happen on a given sleep. The product would become "the model usually behaves." That is the failure mode of every memory system Remory exists to differ from.

This is why the merge stage is one Python loop issuing one backend call per section. It is not a single prompt with sections delimited by headings. The shape lives in `sleep/orchestrator.py`, not in the prompts. A prompt that says "rewrite only section X" but ships the full `state.md` in context does not satisfy this rule, and a contributor adding such a thing has broken the architectural property the rest of the design rests on.

The cost is real: a topic with five mergeable sections pays five round trips during merge, plus extract and (when configured) critique. We accept that cost because falsifiability is what makes the design defensible. The reviewer can read the merger's call site, confirm the prompt construction, and assert by inspection that cross-section drift is physically impossible. A "smart" merger that promises not to drift is not the same artefact.

## Sleep pipeline stages

The sleep pipeline (`INSTRUCTIONS.md` §7) runs three stages: extract, merge, and — when configured — critique. Each invokes the LLM backend headlessly with a bundled prompt template.

**Extract** reads all pending raw entries for the topic and the schema, and emits a structured JSON document of candidate updates keyed by section id. Each candidate carries the text of the proposed update and a pointer to the raw file it came from. The extractor is the only stage that sees the full raw input; it is also the only stage that needs to, because its job is to route. If the JSON is malformed, the pipeline retries once with stricter instructions; on second failure, sleep aborts cleanly and the state remains untouched.

**Merge** is the load-bearing step. For each non-`append_only` section that has candidate updates, the orchestrator issues one headless backend call whose context contains that section's current text and the candidates routed to it — nothing else. Sections marked `append_only: true` in the schema bypass the merger entirely: their raw text is appended verbatim with a date stamp, because some sections (the workout schema's `recent_sessions` log, the job-profile schema's `evidence_log`) are evidence that should accumulate without model rewriting. The `append_only` flag is part of the schema spec — see the [Sections section in `schemas.md`](./schemas.md#sections) for the field's contract.

**Critique** runs only when the schema's `sleep.default_depth` is `merge_and_critique`. The critic reads the full updated `state.md` and writes its observations to `_review.md`. It never modifies `state.md`. A concrete example of what it catches: two weeks ago you told the job-profile topic you wanted to optimise for autonomy; this week you brought up a team-lead role and seemed enthusiastic. The merger, scoped to one section at a time, has no view across that contradiction. The critic does, and surfaces it in `_review.md` for the user to read on their own time. The path to `_review.md` is printed at the end of sleep; it is never auto-opened.

## Backend abstraction

Remory talks to the LLM through a `Backend` protocol defined in `src/remory/backends/base.py`. Two implementations ship: `ClaudeCodeBackend` is the default and is the only one exercised by CI; `AnthropicAPIBackend` is a stub that documents the protocol contract against the Anthropic Messages API for users who prefer metered API and for contributors who want to develop against the SDK directly. The choice of CLI-as-default is documented in [ADR-0009](./adr/0009-claude-cli-default-backend.md).

The protocol's shape is the small surface every backend has to satisfy: a `chat()` method that launches an interactive session in a given working directory and returns when the session ends; a `headless()` method that runs a single non-interactive invocation with a prompt, an optional subagent name, and a timeout, used by the sleep pipeline's three stages; and a `health_check()` method used by `remory doctor`. `ClaudeCodeBackend` implements each by shelling out to `claude` with the right flags and reading the JSONL transcript Claude Code already writes under `~/.claude/projects/`. The interactive surface is TTY-attached and blocks until exit; the headless surface captures stdout, retries transient failures with backoff, and is the seam the sleep stages call into.

## The Claude Code orchestration layer

Remory is not just a Python program that calls an LLM. It is a harness on top of Claude Code, which means most of its production-runtime behaviour is configured through subagents, slash commands, and hooks that live in a `.claude/` directory the user's machine reads from. The full surface is described in `INSTRUCTIONS.md` §10.

**Subagents.** Four production subagents drive the runtime: `extractor` produces candidate updates from raw entries (read-only); `merger` rewrites a single section given its current text and candidates (no tools); `critic` writes `_review.md` from the full updated state (read + restricted write); `wizard` runs the first-run conversation that produces `about-me.md` and the initial per-topic `meta.yaml` (read + write). Each subagent is a markdown file with YAML frontmatter naming the agent and its allowed tools, followed by the system prompt. The Python orchestrator addresses them by name via `claude --agent <name>`.

**Slash commands and hooks.** Slash commands are user-invoked shortcuts that work inside a chat session: `/sleep`, `/state`, `/recent`, `/review`. Two hooks carry policy. A `PreToolUse` hook rejects any `Edit` or `Write` against `state.md` during chat — the state is updated only by sleep, never by chat — so the read-only property survives a misbehaving model. A `SessionEnd` hook fires when the chat session ends and acts as a safety net for the raw-entry write; it defers to the chat parent when the parent is still holding the topic lock, scanning by `session_id` for an existing raw entry as a belt-and-braces idempotency floor. The coordination policy is recorded in [ADR-0002](./adr/0002-chat-vs-session-end-hook-raw-write-coordination.md).

**Two `.claude/` directories, two audiences.** This is the footgun. The `.claude/` directory committed to the Remory source repository contains _dev-time_ subagents: `architect` (proposes module layouts), `implementer` (writes code to a settled plan), `reviewer` (does a phase-end code-review pass). These are used by contributors while building Remory. They are not shipped to users. They never run against user data. Separately, when an end user runs `remory init`, the bundled production templates are materialised into `<data_dir>/.claude/` — that is where the _production-time_ subagents (`extractor`, `merger`, `critic`, `wizard`) live, along with the slash commands and hooks above. Two different `.claude/` directories, at two different paths, serving two different audiences. Confusing them — for example, by putting a production subagent definition in the repo's `.claude/agents/` — silently breaks the separation. The rule that user data lives outside the repo (see [ADR-0012](./adr/0012-data-dir-outside-repo.md)) is what makes this separation enforceable rather than merely conventional. `CLAUDE.md`'s "dev-time vs production-time `.claude/`" paragraph is the canonical statement of this rule.

**Where the templates live in the source tree.** The bundled `.claude/agents/*.md`, `.claude/commands/*.md`, and `.claude/settings.json` that `remory init` writes into the user's data directory are sourced from `src/remory/` (the package data). They are not in the repo's `.claude/`. They are not user-editable: `remory init --refresh` rewrites them from the package copy on demand, and `remory doctor` notes drift in the meantime. The reasoning is in [ADR-0011](./adr/0011-bundled-prompts.md).

## Why no telemetry

Remory ships with no telemetry, no analytics, no crash reporting, no phone-home behaviour. This is not a setting that can be flipped on; it is an architectural property of the project. The threat model is single-user, single-machine, and the user has already chosen which conversations to send to Anthropic via Claude Code. Layering "anonymous usage metrics" on top of that would be inserting a second telemetry surface the user did not opt into for a benefit they did not ask for. `INSTRUCTIONS.md` §15 names this exclusion explicitly, alongside the other things v0.1 deliberately does not do.

## Why no vector DB in v0.1

The recency-bias problem Remory exists to solve is solved by _structure_, not by retrieval. Section isolation, plus an `evidence_log` section that accumulates dated pointers back to raw entries, plus the critic's cross-section pass, is the v0.1 answer. Adding a vector database to "improve" merge would pull in a v0.3-territory dependency (`INSTRUCTIONS.md` §15) to fix a problem the structural answer already addresses. v0.3 may revisit semantic recall as a distinct feature — there are plausible uses, like surfacing forgotten raw entries during chat — but the v0.1 answer to the memory problem is intentionally structural, and that is the design contract.

## Why bundled prompts (no user override)

Prompt templates live in `src/remory/sleep/prompts.py` and in the bundled `.claude/agents/*.md` templates that `remory init` materialises into the user's data directory. They are not user-overridable. Users tune behaviour through the per-schema `tone` and `strictness` knobs, which the templates interpolate. The contract is: same Remory version installed, same prompts running, same behavioural envelope across users. A user who genuinely needs different prompts forks the repo — that is an acceptable, deliberate outcome. The cost of accepting prompt forks downstream is much lower than the cost of accepting prompt edits in-place: forks are honest about being a different product; in-place edits are invisible from the outside and turn every support request into "what does your prompt look like." The full reasoning is in [ADR-0011](./adr/0011-bundled-prompts.md).

## File layout

The user-on-disk layout is reproduced here for reference. The data directory lives at `$XDG_DATA_HOME/remory/` on Linux (and the platform-appropriate equivalent on macOS), resolved via `platformdirs`. It is never inside the source tree — see [ADR-0012](./adr/0012-data-dir-outside-repo.md).

```
$XDG_DATA_HOME/remory/
|-- about-me.md                   # written by the wizard; meta-context
|-- config.toml                   # user-editable preferences
|-- topics/
|   |-- job-profile/
|   |   |-- CLAUDE.md             # auto-generated; tells Claude Code how to behave for THIS topic
|   |   |-- state.md              # YAML frontmatter + sectioned markdown body
|   |   |-- meta.yaml             # last_consolidated, pending_count, schema_version, knobs
|   |   |-- raw/
|   |   |   |-- 2026/
|   |   |   |   |-- 2026-05-07-1820.md
|   |   |   |   |-- 2026-05-09-0930.md
|   |   |-- _review.md            # critic output (overwritten each sleep)
|   |   `-- .backups/
|   |       `-- state.md.2026-05-07-1820.bak
|   |-- workout/
|   `-- coaching/
`-- logs/
    `-- remory.log
```

For the source-repo layout (where `src/remory/` lives, how tests are organised, where the bundled `.claude/` templates sit in the package), see `INSTRUCTIONS.md` §3.

## Locking and atomicity

Every operation that reads or writes topic state acquires a topic-scoped file lock. The lock prevents `remory chat` and `remory sleep` from colliding on the same topic (the chat parent holds the lock continuously across the subprocess; sleep acquires it for the duration of the pipeline) and also keeps a `remory sleep --if-due` cron invocation from racing an interactive session. Locking is a thin wrapper around `fcntl.flock` on POSIX; the lock file lives next to the topic data and is reaped on release.

Atomic writes are required for `state.md`. The write path is: write to `state.md.tmp` in the same directory, `os.fsync` the file descriptor, rename over `state.md`. The rename is atomic on POSIX. A timestamped backup is taken into `.backups/state.md.<ts>.bak` before sleep attempts any write — non-negotiable. There is a durability nuance on Darwin: `os.fsync` flushes the kernel page cache but not the device's hardware write cache, so a power-loss event between the rename and the cache flush can lose data. The trade-off is documented in [ADR-0001](./adr/0001-fsync-on-darwin.md); v0.1 accepts the gap because the realistic failure mode for a single-user local tool is process crash, not power loss.

## What lives where

The new-contributor cheatsheet. The same artefact often has a source-repo home (where it is authored) and a user-data-dir home (where it ends up at runtime). Confusing the two is the most common source of "why isn't my change taking effect?" questions.

Two distinct delivery patterns appear in the table below. Schemas and sleep-pipeline prompt templates are **loaded from the installed package at runtime** — they are never copied onto the user's disk, so a Remory upgrade picks them up automatically. Subagent definitions, slash command definitions, and hook scripts are **materialised to disk by `remory init`** into `<data_dir>/.claude/` so that the `claude` CLI can find them; the originals still live in the package, and `remory init --refresh` rewrites the on-disk copies. The asymmetry is deliberate: the package-loaded artefacts are Python's, so Python can read them directly; the materialised artefacts are Claude Code's, and Claude Code expects them on the filesystem.

| What | Where (source repo) | Where (user data dir) |
| --- | --- | --- |
| Built-in topic schemas | `src/remory/schemas_builtin/*.yaml` | (not copied; loaded from package at runtime) |
| User topic schemas | (not in repo) | `$XDG_CONFIG_HOME/remory/schemas/*.yaml` |
| Sleep-pipeline prompt templates | `src/remory/sleep/prompts.py` | (not copied; loaded from package at runtime) |
| Production subagent definitions | `src/remory/` package data | `<data_dir>/.claude/agents/*.md` (materialised by `remory init`) |
| Production slash command definitions | `src/remory/` package data | `<data_dir>/.claude/commands/*.md` (materialised by `remory init`) |
| Production hook settings | `src/remory/` package data | `<data_dir>/.claude/settings.json` (materialised by `remory init`) |
| Production hook scripts | `src/remory/` package data | paths declared in `<data_dir>/.claude/settings.json` |
| Dev-time subagent definitions | `.claude/agents/architect.md`, `implementer.md`, `reviewer.md` | (not shipped; dev-only) |
| Per-topic `state.md` | (never in repo) | `<data_dir>/topics/<name>/state.md` |
| Per-topic `CLAUDE.md` | (never in repo) | `<data_dir>/topics/<name>/CLAUDE.md` |
| Raw entries | (never in repo) | `<data_dir>/topics/<name>/raw/<year>/*.md` |
| Backups of `state.md` | (never in repo) | `<data_dir>/topics/<name>/.backups/*.bak` |
| Logs | (never in repo) | `$XDG_STATE_HOME/remory/logs/remory.log` |
| User config | (never in repo) | `$XDG_CONFIG_HOME/remory/config.toml` |

The rule the table encodes: nothing the user authored, said, or generated ever lives in the source tree. See [ADR-0012](./adr/0012-data-dir-outside-repo.md) for the full statement of this rule and the resolver that enforces it at startup.
