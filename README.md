# Remory

> A second brain that actually remembers.

[![CI](https://github.com/jlinnenkamp/remory/actions/workflows/ci.yml/badge.svg)](https://github.com/jlinnenkamp/remory/actions/workflows/ci.yml)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL--3.0--or--later-blue.svg)](LICENSE)

Remory is a terminal-based personal-assistant harness on top of Claude Code that gives you persistent, topic-scoped conversations whose memory you can actually read.

## Why Remory exists

Long-running conversations with an LLM degrade in a particular way: the model weights the most recent turn highest and the oldest turns lowest, so the picture it has of you drifts toward whatever you said this week. Tools that paper over this with vector search and summarisation rarely surface the drift; they just give the drift a new shape. If you have ever caught a chatbot confidently telling you something about yourself that was true two months ago and false today, you have met the recency-bias problem.

Remory's answer is to keep the conversation and the memory in separate files, and to consolidate one into the other on a deliberate schedule the user controls. Each topic — a job search, a workout plan, a coaching thread — has a `state.md` file you can read in any editor. A `sleep` cycle takes new conversation transcripts and folds them into that file one section at a time, with each section's update isolated from the others so the model literally cannot drift from "skills" into "values" mid-thought. The user is always one `cat state.md` away from seeing exactly what Remory thinks it knows.

## Quickstart

Requires Python 3.12+, [pipx](https://pipx.pypa.io/), and the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) installed and logged in.

```bash
pipx install git+https://github.com/jlinnenkamp/remory.git
remory init                    # first-run wizard; picks topics, sets tone, writes data dir
remory chat workout            # have a conversation
remory sleep workout           # consolidate the conversation into state.md
remory state workout           # read what Remory now knows
```

Your data lives in `$XDG_DATA_HOME/remory/` (typically `~/.local/share/remory/` on Linux, `~/Library/Application Support/remory/` on macOS). Nothing is ever written inside this source repo.

## Built-in topics

**`job-profile`** — Interviews, self-reflection, and notes from people you have spoken with about your career direction. Sleep produces an evolving picture of your optimal job profile: skills, values, hard constraints, options considered, current leaning, and an evidence log that traces every claim back to a raw conversation. Tone defaults to warm; you can switch it to direct during `remory init`.

**`workout`** — A living workout plan plus session logs. After each conversation about what you actually did in the gym, sleep updates the plan, progressions, and notes-and-injuries sections. Single-pass merge by default (no critic stage) because workout state is short and self-correcting.

**`coaching`** — Q&A from therapy or coaching sessions. Insights accumulate across themes over time, and the critic stage flags contradictions between sessions ("two weeks ago you wanted X; this week you want Y") so the pattern is visible to you rather than smoothed over. Tone defaults to warm and gentle.

## Data and privacy

Remory is a local-first tool. There is no Remory server, no account, no telemetry, no crash reporting, no analytics. The project does not phone home in any form, opt-in or otherwise. This is an architectural property, not a setting.

**Where your data lives:** all conversation transcripts, consolidated `state.md` files, and per-topic metadata live in `$XDG_DATA_HOME/remory/` on your machine. Configuration lives in `$XDG_CONFIG_HOME/remory/`. Logs live in `$XDG_STATE_HOME/remory/logs/`. None of these directories is ever inside the Remory source repo. You can delete the data directory at any time and Remory will start over the next time you run `remory init`. The boundary between "your data" and "the Remory source tree" is enforced by a startup check; see [ADR-0012](docs/adr/0012-data-dir-outside-repo.md).

**What Anthropic sees:** Remory drives the official `claude` CLI as a subprocess. Anything you type during `remory chat`, plus the bundled prompts Remory sends during `remory sleep`, is transmitted to Anthropic exactly as it would be if you used `claude` directly. Remory does not add a middleman; it does not log your prompts to any third party. The privacy posture of `remory chat` is identical to the privacy posture of `claude` itself, governed by [Anthropic's usage policies](https://www.anthropic.com/legal/aup).

**Versioning your own brain:** because Remory's state lives in plain markdown, some users will want to keep their data directory in a private git repo. This is supported and encouraged. A starter `.gitignore` for that case:

```gitignore
# Backups, locks, and temp files Remory writes during sleep.
.backups/
*.lock
*.tmp

# Logs are local-only by design and rarely useful to track.
logs/
```

Track `state.md`, `meta.yaml`, the per-topic `CLAUDE.md`, and the `.claude/` runtime — those define how your brain behaves, and `remory init` regenerating them on a fresh clone would erase any local customisations you made.

We strongly recommend such a repo is **private**. `state.md` files contain a frank, distilled picture of you.

## Architecture

The chat flow produces raw entries. A `remory chat <topic>` invocation acquires a topic lock, launches Claude Code in the topic's working directory, and on clean exit captures the JSONL transcript as a new dated raw entry under `raw/<year>/`.

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

The diagram above shows the chat surface. The model can read `state.md` as context but a `PreToolUse` hook rejects any attempt to modify it during chat — `state.md` is read-only outside sleep, enforced by hook rather than just by prompt instruction. The threshold nudge at the bottom appears once per crossing and is never modal; nothing nags.

The sleep flow consolidates pending raw entries into `state.md`. It runs three stages — extract, merge, and (optionally) critique — inside the same topic lock the chat surface would acquire, so the two flows cannot interleave on the same topic.

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

The sleep flow's load-bearing step is the merger loop in the middle: one LLM call per section, with no other section's text in context. A timestamped backup of `state.md` lands in `.backups/` before any write attempt; the final write is temp-file plus fsync plus rename, so a partial failure cannot corrupt the live file.

The single load-bearing architectural property is **section isolation**: the merge step runs once per section and sees only that section's text. The full story lives in [`docs/architecture.md`](docs/architecture.md) and [ADR-0008](docs/adr/0008-section-isolated-merges.md).

## What this is not

- **Not a chatbot wrapper.** Remory does not wrap Claude Code's chat UI; it sits beside it and gives the conversation a place to live.
- **Not a vector database.** No embeddings, no semantic recall, no nearest-neighbour. State is structured markdown the user can read.
- **Not a SaaS.** No accounts, no server, no cloud. Your data sits on your filesystem.
- **Not [Mem.ai](https://mem.ai).** Mem.ai is a managed note-taking product. Remory is a local consolidation harness for LLM conversations.
- **Not [Rewind](https://rewind.ai).** Rewind records your screen. Remory records nothing it was not handed in a conversation.
- **Not a Notion plugin.** No third-party integrations in v0.1. Files are markdown; integrate at the filesystem layer if you must.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, the fake-claude testing pattern, and the schema authoring guide. Security issues go to [SECURITY.md](SECURITY.md).

## License

[AGPL-3.0-or-later](LICENSE). If you run a modified version of Remory as a service for others, the AGPL's network-use clause applies — but Remory is a local CLI, so the network-use clause rarely activates.
