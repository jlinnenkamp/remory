# Phase 6 — Claude Code subagents and hooks (binding consolidated plan)

This document is the binding contract for Phase 6 implementation. It consolidates three architect passes plus user-applied refinements. **Phase 6 implements; it does not redesign. Strings are not subject to revision in Phase 6.** Any deviation is an ambiguity to surface, not an improvement to make. The escalation rule applies: if a fourth gap is found at implementer time, the implementer stops and reports — it does not paper over with a "reasonable default."

Spec anchors: `INSTRUCTIONS.md` §10, §11, §14. Memory: `feedback_no_silent_data_loss`, `feedback_wire_format_enums`, `feedback_log_omit_prompt_adjacent_fields`, `feedback_serialization_format_choice`, `feedback_test_names_encode_contract`, `feedback_changelog_format`, `feedback_adr_for_known_gaps`, `feedback_silently_means_logged`, `feedback_ux_phase_concrete_strings`, `project_phase3_backup_atomicity`, `project_phase4_chat_vs_sessionend_coordination`. ADRs: 0002 (chat-vs-hook ownership; load-bearing), 0003 (wizard commit partial-failure leave-as-is — preserved verbatim), 0004 (wizard SIGINT Windows best-effort — preserved verbatim).

## 1. User-confirmed decisions

**D1. Threshold nudge is owned by `chat_cmd` only.** SessionEnd hook never prints. ADR-0002 governs. New ADR-0006 captures the chat-only nudge policy. `INSTRUCTIONS.md` §10 is edited (§5.14 below, user-approved verbatim).

**D2. Wizard rearchitected as claude-driven.** The `wizard.md` subagent (a Claude Code subagent) drives the interview turns; the Python harness handles preflight, JSON validation, recovery on hard fail, and the COMMIT block. The COMMIT block is preserved verbatim from Phase 5 (per-topic-atomic + SIGINT guard). Hard precondition: claude binary on PATH AND authed before launch. No offline fallback; if claude is unreachable, point at `remory doctor`. New ADR-0005 captures the rationale.

**D3. `remory init --refresh [--force] [--dry-run]` ships in Phase 6.** Idempotently rewrites the data-dir `.claude/` tree from bundled templates and eagerly regenerates every topic's `CLAUDE.md`. Does NOT touch `state.md`, `meta.yaml`, `raw/`, `_review.md`, `about-me.md`. Stamp-aware: preserves unstamped files (even with `--force`); refuses-with-conflict on stamped-but-edited (requires `--force`); writes `.bak` for every overwrite including the stamped-older common case.

**D4. Wizard transcript skip is cwd-based.** Harness launches `claude --agent wizard` with `cwd=eff_data_dir` (the data directory root, not a topic dir). The SessionEnd hook's existing "cwd not under `<data_dir>/topics/<slug>/`" branch returns `no_topic` and exits silently. No new env var, no settings flag. This is **load-bearing**; do not change without re-thinking the SessionEnd contract. Documented as a one-line addendum to ADR-0002.

**D5. `--force` does NOT overwrite unstamped files.** Deliberate asymmetry. `--force` overrides the "stamped-but-edited refuse" branch only. An unstamped file has no evidence of remory ownership; we don't claim it. Users who want to nuke unstamped files can `rm` and re-run.

**D6. Production sleep subagents (`extractor`, `merger`, `critic`): files installed but unused in Phase 6.** `sleep/orchestrator.py` migration to `--agent=...` invocation is deferred to Phase 7 with a tracked TODO in the orchestrator. The bundled `.md` files are real artefacts so `--refresh` lands real content; they are not no-ops, just unused by the current sleep flow. Phase 7 will wire them.

**D7. `/sleep` slash command prints "exit and run", does NOT shell out to Bash.** Less surface, no lock contention with the chat lock. Matches the §15 pin "sleep is deliberate, manual, separate."

**D8. `Backend.chat` gains `agent: str | None = None`.** Single-line surface change on `base.py` and `claude_code.py`, mirrored in `fake_backend.py`. The `chat_cmd` callsite continues to pass `agent=None`. Real-CLI verification that interactive `claude --agent wizard` works is a deferred smoke test, written as a PR-description checkbox (§14).

**D9. Hook stdin payload format.** The hook entry points read JSON from stdin per claude's hook protocol. Parse permissively (`extra="ignore"`); accept both `session_id` and `sessionId`; fall back to env/argv where a field is missing. Pin keys we depend on in a comment block + snapshot-test against one recorded payload fixture per hook event. Real shape verification is the second PR-description checkbox (§14).

## 2. ADR numbering correction

Phase 5 already shipped ADRs 0003 and 0004. The architect drafts used 0003/0005/0006 by mistake. **Corrected numbering for Phase 6:**

- `docs/adr/0005-claude-template-backups-retention.md` (was 0003 in architect draft)
- `docs/adr/0006-wizard-claude-driven-interview.md` (was 0005)
- `docs/adr/0007-session-end-hook-never-prints.md` (was 0006)

Plus a one-line addendum to `docs/adr/0002-chat-vs-session-end-hook-raw-write-coordination.md` recording the wizard's `cwd=eff_data_dir` choice (D4).

## 3. Module split

### Bundled data templates (new, under `src/remory/data_templates/`)

```
src/remory/data_templates/
    __init__.py                     importlib.resources accessor
    .claude/
        agents/
            wizard.md               §5.1 body
            extractor.md            §5.2 body
            merger.md               §5.3 body
            critic.md               §5.4 body
        commands/
            sleep.md                §5.5 body
            state.md                §5.5 body
            recent.md               §5.5 body
            review.md               §5.5 body
        settings.json               §5.6 bytes
```

Every markdown template carries `<!-- remory: template_version=1 -->` immediately after the YAML frontmatter. `settings.json` carries top-level `"_remory_template_version": 1`.

### New source modules

```
src/remory/
    claude_assets.py                 install / refresh / stamp / detect / EmitResult / emit_backup
    topic_claude_md.py               per-topic CLAUDE.md generator + regen_all_topic_claude_md
    hooks/
        __init__.py                  Typer subapp registered as `remory _hook`
        session_end.py               SessionEndInput, SessionEndOutcome, run(), main()
        pre_tool_use.py              PreToolUseInput, PreToolUseDecision, decide(), main()
    wizard/
        _subagent.py                 _launch_subagent_and_collect, _validate_answers_json,
                                     _write_run_dir, _dump_recovery; Pydantic models for
                                     the answers.json wire surface
```

### Modified modules

```
src/remory/
    wizard/
        __init__.py                  drop WizardThreeStrikesError; add WizardPreflightError,
                                     WizardAnswerParseError, WizardSubagentFailedError
        _orchestrator.py             REWRITE to claude-driven flow (§7)
        _answers.py                  promote to Pydantic (§6.1)
        _commit.py                   tolerate Pydantic WizardAnswers (attribute access)
        _strings.py                  shrink per §5.9
    backends/
        base.py                      add agent: str | None = None to Backend.chat Protocol
        claude_code.py               pass --agent <name> when set
    cli/__init__.py                  add --refresh, --force, --dry-run flags to init;
                                     register _hook subapp
    commands/
        init_cmd.py                  call install_data_dir_templates after topic create;
                                     use topic_claude_md.render() instead of placeholder
        doctor_cmd.py                template + CLAUDE.md confirmation checks per §5.11
    templates.py                     keep CLAUDE_MD_PLACEHOLDER as deprecated alias for one release
tests/
    fakes/
        fake_backend.py              mirror agent param on chat
        fake_claude                  add FAKE_CLAUDE_MODE=wizard_interactive + failure variants
```

### Files to delete

```
src/remory/wizard/_steps.py
src/remory/wizard/_letter.py
src/remory/wizard/_validators.py
tests/unit/test_wizard_letter.py
tests/unit/test_wizard_validators.py
tests/unit/test_wizard_three_strikes.py
```

Plus rewrite `tests/unit/test_wizard_orchestrator.py` (new content per §11 test surface) and shrink `tests/unit/test_wizard_strings.py` (rename to `test_wizard_messages.py`; covers only kept entries from §5.9).

## 4. Wire-format pins

### 4.1 `answers.json` (wizard subagent → harness)

Pydantic model. `model_config = ConfigDict(frozen=True, extra="forbid")`. Wire format; `version` is the forward-compat hook per memory `feedback_wire_format_enums`.

```python
class WizardKnobs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tone: Literal["warm", "balanced", "direct"]
    strictness: Literal["gentle", "balanced", "rigorous"]

class WizardAnswers(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1]
    name: str | None
    chosen_topics: tuple[str, ...]
    knobs_by_topic: dict[str, WizardKnobs]
    wish: str | None
```

`letter: str` is NOT a field; the subagent writes `letter.md` separately. Harness passes letter alongside `answers` into `commit()`.

### 4.2 Template version stamp

Markdown files: `<!-- remory: template_version=1 -->` as the first non-frontmatter line.

`settings.json`: `"_remory_template_version": 1` as a top-level key.

```python
PRODUCTION_TEMPLATE_VERSION: Final[int] = 1
TEMPLATE_VERSION_KEY: Final[str] = "_remory_template_version"
```

Wire format. Bumping the int is forward-compat. Renaming the key requires the same migration plan as `RawStatus`.

### 4.3 `EmitResult` and `SkippedEntry`

```python
class SkippedEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: Path
    reason: Literal["unstamped-preserved", "stamped-but-edited", "unchanged"]
    current_version: int | None
    on_disk_version: int | None

class EmitResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    written: tuple[Path, ...]        # new files
    overwritten: tuple[Path, ...]    # existing files replaced (with .bak)
    skipped: tuple[SkippedEntry, ...]
    dry_run: bool
```

`refresh(data_dir, *, force, dry_run) -> EmitResult` combines `.claude/` actions and per-topic CLAUDE.md actions in one structure, distinguished by path prefix (`.claude/...` vs `topics/<slug>/CLAUDE.md`).

### 4.4 `.bak` path layout

`<data_dir>/.claude/.backups/<flattened-relative-path>.<UTC-iso-timestamp>.bak`

- Flatten slashes to `__` (Windows-safe; flat backups dir; easier to `ls`).
- Timestamp: UTC ISO with colons replaced by hyphens (Windows-safe).
- Example: `<data_dir>/.claude/.backups/agents__extractor.md.2026-05-12T14-23-07Z.bak`
- Per-topic CLAUDE.md backups: `<data_dir>/.claude/.backups/topics__workout__CLAUDE.md.<ts>.bak` (NOT under topic's `.backups/` — the wizard owns this backup space).
- Writes go through `atomic.atomic_write_bytes` (memory `project_phase3_backup_atomicity` applies).

Phase 6 does not ship cleanup. Backups accumulate. ADR-0005 captures the deferral.

## 5. Concrete strings (verbatim — implementer copies these into code)

### 5.1 `wizard.md` subagent body

The harness substitutes `{{run_dir}}` at write time before launching claude. Allowed tools: `Read, Write`.

```markdown
---
name: wizard
description: First-run interview for Remory. Reads built-in topic schemas, asks the user a small number of warm questions, writes structured answers as JSON, then composes a one-paragraph letter.
allowed_tools: [Read, Write]
---
<!-- remory: template_version=1 -->
You are the Remory wizard. The person you are talking to has just installed Remory and is meeting their second brain for the first time. This is the only conversation where they hear your voice before they decide whether to trust it.

Be warm and a little playful. Short turns. One question at a time. Do not lecture. Do not use bullet lists when prose works. Do not ask permission to ask the next question — just ask it.

You have access to these files (read them with the Read tool when you need to):

- `{{run_dir}}/manifest.json` — list of built-in schema files in lex order.
- `{{run_dir}}/schemas/<name>.yaml` — one file per built-in topic. Each schema has a `description`, a `defaults` block (with `tone` and `strictness`), and a `wizard_questions` list.

The interview has six beats. Move briskly.

1. **Greet by name.** "What should I call you?" Use the name once or twice after this, then stop.
2. **Pick topics.** Describe the three built-ins (one short line each — paraphrase from each schema's `description`). Ask which they'd like to set up. Multi-select is fine. They can also pick none (in which case skip to step 5).
3. **Per chosen topic, run that topic's `wizard_questions`.** Two questions per topic. For each question, read the `wizard_questions` entry, present the options conversationally (not as a menu), and accept their answer. If they pause, say "want to skip?" — the schema's `defaults` block carries the fallback values. Map each answer to a `value` from the schema's `options`. If the user describes their preference in words rather than picking, map to the closest option and reflect it back ("sounds like you want [value] — yes?").
4. **One wish question.** "In one sentence — what are you hoping a second brain helps you do?" Accept anything, including "I don't know yet" or a skip. Free text.
5. **Write the answers file.** Use the Write tool to write `{{run_dir}}/answers.json` with exactly this shape (no extra keys, no trailing prose):

   ```json
   {
     "version": 1,
     "name": "...",
     "chosen_topics": ["..."],
     "knobs_by_topic": {
       "<topic>": {"tone": "<tone-value>", "strictness": "<strictness-value>"}
     },
     "wish": "..."
   }
   ```

   Rules:
   - `version` is always the integer `1`.
   - `name` is the user's name as a string, or `null` if they skipped.
   - `chosen_topics` lists topic names (the schema `name` field, e.g. `"workout"`), in the order the user picked them.
   - `knobs_by_topic` has one entry per chosen topic. The `tone` and `strictness` values must be drawn from the `options` block of that topic's schema (e.g. `"warm"`, `"direct"`, `"gentle"`, `"rigorous"`, `"balanced"`). If the user skipped, use the value from that schema's `defaults` block.
   - `wish` is a string or `null` if skipped.

6. **Compose the letter.** After writing `answers.json`, write `{{run_dir}}/letter.md`: one paragraph in second person, 3–5 sentences, reading back what you heard. Reflect the *specific* things the user said, not the topic descriptions. End on a note that signals you'll keep what they bring you. No preamble, no headings, no bullets.

After both files are written, say one short closing line to the user (e.g. "All set — I'll hand you back to the rest of Remory now") and stop. Do not try to launch other commands. Do not edit any other files.

If the user presses Ctrl+C during the conversation, that's fine — nothing has been written yet outside this run directory, and Remory's harness handles the rest.
```

### 5.2 `extractor.md`

```markdown
---
name: extractor
description: Stage 1 of sleep. Reads pending raw entries; emits candidate updates as JSON, keyed by section id.
allowed_tools: [Read]
---
<!-- remory: template_version=1 -->
You are the Remory extractor. You are stage 1 of the sleep pipeline.

You receive: (a) the schema definition for this topic, including its
section ids and descriptions; (b) one or more pending raw conversation
entries.

Your task: produce candidate updates as a single JSON object, keyed by
section id. Each value is an array of {text, evidence} objects.

Rules:
1. Respond with ONLY JSON. No prose, no markdown fences, no commentary.
2. Use exactly the section ids given in the schema. Do not invent ids.
3. Sections with no candidate updates get an empty array.
4. Each "evidence" string is the relative POSIX path of the raw entry
   the candidate came from, e.g. "raw/2026/2026-05-07-1820.md".
5. Each "text" is a single sentence in second person addressing the user.
6. Do not paraphrase out novel concrete facts — preserve specifics
   (dates, names, numbers) verbatim where they appear.
7. If a raw entry contradicts an earlier one, both go in. The merger
   resolves it.
```

### 5.3 `merger.md`

```markdown
---
name: merger
description: Stage 2 of sleep. Rewrites one state.md section, given that section's current text and the candidates for it.
allowed_tools: []
---
<!-- remory: template_version=1 -->
You are the Remory merger. You are stage 2 of the sleep pipeline.

You receive: one section's current text, the schema section
description, the per-topic persona, the user's tone and strictness
knobs, and a list of candidate updates for that section only.

Your task: produce the rewritten section text. Plain markdown. No
heading line (the caller renders the heading). No JSON. No code fences.

Section isolation is load-bearing. You cannot see other sections; do
not invent references to content you don't have. If a candidate
mentions something that obviously belongs in a different section,
drop it — another merger call will see it.

Tone, strictness, and persona for this run are interpolated below
this header by the caller.
```

### 5.4 `critic.md`

```markdown
---
name: critic
description: Stage 3 of sleep. Reads the full updated state.md; writes _review.md with contradictions and thin sections.
allowed_tools: [Read, Write]
---
<!-- remory: template_version=1 -->
You are the Remory critic. You are stage 3 of the sleep pipeline.

You receive: the full updated state.md after stage-2 merges, plus the
schema and the user's knobs.

Your task: write a short, useful _review.md (markdown, no frontmatter).
Surface, in order:
1. Possible contradictions across sections.
2. Sections that look thin or stale relative to the schema description.
3. Claims that don't appear to trace back to the evidence log.

Rules:
- You may Read state.md and other files in the topic directory.
- You may Write only to _review.md.
- Do not modify state.md. The user reads your review on their own time.
- Be brief. The user can act on three items; not on twenty.
```

### 5.5 Slash commands

**`commands/sleep.md`**:
```markdown
---
description: Consolidate pending raw entries for the current topic into state.md.
---
<!-- remory: template_version=1 -->
Sleep is a deliberate, separate step in Remory — it runs outside the chat session, not inside it. To consolidate this topic's pending entries:

1. Exit this chat session (Ctrl+D, or `/exit` if your terminal supports it).
2. Run `remory sleep <topic>` where `<topic>` is the name of this directory (the basename of `pwd`).

You'll see a summary when it finishes, and `_review.md` will be updated if the topic's schema runs critique.
```

**`commands/state.md`**:
```markdown
---
description: Show this topic's current state.md.
---
<!-- remory: template_version=1 -->
Read `state.md` in the current directory and present it as-is.
Do not modify it.
```

**`commands/recent.md`**:
```markdown
---
description: List the last 5 raw entries.
---
<!-- remory: template_version=1 -->
Read the five most recently modified files under `raw/` in the current directory and present a short list: filename, created timestamp (from the YAML frontmatter), and the first line of body content. Do not modify any files.
```

**`commands/review.md`**:
```markdown
---
description: Show the last critic review (_review.md).
---
<!-- remory: template_version=1 -->
Read `_review.md` in the current directory and present it as-is.
If the file does not exist, say so plainly — it means no critique-depth
sleep has run yet.
```

### 5.6 `settings.json` (literal bytes; snapshot-tested)

```json
{
  "_remory_template_version": 1,
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "remory _hook session-end",
            "timeout": 30
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "remory _hook pretool",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Two-space indentation, trailing newline at EOF. Snapshot test pins exact bytes (`test_data_templates_snapshot.py::test_settings_json_bytes_byte_pinned`).

The hook command is bare `remory _hook session-end` — relies on `remory` being on PATH at hook-invocation time. If a future user reports breakage when claude is launched outside the shell that has `remory` on PATH, we add `--remory-cmd <abs>` to `install_data_dir_templates` and resolve at install time. Out of scope for Phase 6; surfaced as a known limit in ADR-0005.

### 5.7 Per-topic CLAUDE.md template

Stamped + interpolated. Byte-stable for fixed inputs (snapshot-tested per (schema, tone, strictness) tuple).

```
<!-- remory: template_version=1 -->
# Topic: {schema_name}

You are the assistant for the user's "{schema_name}" topic in Remory.
Read `state.md` at the start of each session — it is your canonical
context for what is already known about this topic. Treat `state.md`
as read-only. You will be blocked at the tool level from editing it
during this chat (sleep is the only writer).

## Persona for this topic

{persona}

## How the user wants to be spoken to

{tone_line}
{strictness_line}

## Practical rules

- Do not edit `state.md`. It is updated only during sleep.
- Do not write new files outside the topic directory.
- If something the user says contradicts `state.md`, surface the
  contradiction; do not silently overwrite the older view.
- Slash commands available in this session: `/sleep`, `/state`,
  `/recent`, `/review`.

## Pointer

The canonical context for this topic is in `state.md`. The user's
broader self-description (name, the wish they brought to Remory) is
in `../../about-me.md`.
```

**`{tone_line}` dispatch table** (literal strings, indexed by `knobs.tone`):
- `warm` → `Warm. Meet the user where they are; flag contradictions kindly.`
- `balanced` → `Balanced. Acknowledge feelings, but be useful first.`
- `direct` → `Direct. Skip the warm-up; say what you see.`

**`{strictness_line}` dispatch table** (literal strings, indexed by `knobs.strictness`):
- `gentle` → `Gentle. Take the user's claims as offered unless evidence in state.md says otherwise.`
- `balanced` → `Balanced. Test claims lightly when they conflict with state.md.`
- `rigorous` → `Rigorous. Stress-test claims; ask for evidence before accepting big changes.`

`{persona}` comes from the schema's `persona` field (already in `src/remory/schemas_builtin/*.yaml`).

`{schema_name}` is the schema name field (e.g. `"workout"`).

### 5.8 PreToolUse refusal stderr

```
state.md is updated only during `remory sleep`. Refusing the write.
```

(Trailing newline.) Surfaced verbatim to the user in the claude session when the hook fires.

### 5.9 `_strings.py` reuse policy

**KEPT VERBATIM from Phase 5** (byte-stable, no edit):
- `PRE_COMMIT_INTERRUPT_MESSAGE`
- `DURING_COMMIT_INTERRUPT_MESSAGE`
- `PARTIAL_FAILURE_WITH_PRIOR_TEMPLATE`
- `PARTIAL_FAILURE_NO_PRIOR_TEMPLATE`
- `ABOUT_ME_FAILURE_MESSAGE`
- `OUTRO_SINGULAR_TEMPLATE`
- `OUTRO_PLURAL_TEMPLATE`

**DELETED**:
- `WELCOME_TEMPLATE`, `STEP_NAME_PROMPT`, `PICK_TOPICS_PROMPT`
- `JOB_PROFILE_PREAMBLE`, `JOB_PROFILE_Q1`, `JOB_PROFILE_Q2`
- `WORKOUT_PREAMBLE`, `WORKOUT_Q1`, `WORKOUT_Q2`
- `COACHING_PREAMBLE`, `COACHING_Q1`, `COACHING_Q2`
- `STEP_WISH_PROMPT`
- `LETTER_PRECALL`, `LETTER_LEAD_IN`
- `THREE_STRIKES_MESSAGE`

**NEW** (drafted in this plan; byte-stable from here):

```python
PRECONDITION_NEEDS_DOCTOR_MESSAGE: Final[str] = (
    "Remory needs the claude CLI to be installed and logged in before the wizard can run.\n"
    "Run: remory doctor\n"
    "Then re-run: remory init\n"
)

RECOVERY_MESSAGE_TEMPLATE: Final[str] = (
    "The wizard couldn't produce valid answers (tried twice).\n"
    "What you said is saved at:\n"
    "  {recovery_dir}\n"
    "No topic files were written. You can re-run `remory init` to try again.\n"
)
```

No Phase 5 byte-pinned string is altered by Phase 6. If any kept entry needs new wording, that is a Phase 5 contract revision and must be surfaced separately, not silently rewritten.

### 5.10 `remory init --refresh` user-visible output

**Non-dry-run, with changes** (example):
```
Refreshed .claude/ templates at <data_dir>/.claude/
  write     agents/extractor.md           (missing)
  overwrite agents/wizard.md              (stamp older: file=1, bundle=2; .bak saved)
  conflict  commands/sleep.md             (stamp current but file edited; --force to overwrite)
  preserve  agents/merger.md              (no stamp — likely user-authored)
  unchanged 6 file(s)
Per-topic CLAUDE.md:
  regenerate topics/workout/CLAUDE.md     (knobs changed; .bak saved)
  unchanged  3 file(s)
```

**Non-dry-run, nothing to do**:
```
.claude/ at <data_dir>/.claude/ is up to date.
Per-topic CLAUDE.md is up to date for all <N> topic(s).
```

**Dry-run, with changes pending**:
```
Would update .claude/ templates at <data_dir>/.claude/:
  write     agents/extractor.md           (missing)
  overwrite agents/wizard.md              (stamp older: file=1, bundle=2)
  conflict  commands/sleep.md             (stamp current but file edited; --force required)
  preserve  agents/merger.md              (unstamped — likely user-modified)
  unchanged 6 file(s)
Would update per-topic CLAUDE.md:
  regenerate topics/workout/CLAUDE.md     (knobs changed)
  regenerate topics/coaching/CLAUDE.md    (template version older)
  unchanged 2 file(s)
Run without --dry-run to apply (a .bak will be saved for each overwrite).
```

**Dry-run, nothing would change**:
```
.claude/ at <data_dir>/.claude/ is up to date.
Per-topic CLAUDE.md is up to date for all <N> topic(s).
```

Column rules:
- Two-column action labels: left-aligned, fixed width (10 chars: `write`, `overwrite`, `conflict`, `preserve`, `unchanged`, `regenerate`).
- Paths quoted as relative-from-data-dir.
- Reason in parentheses; not in quotes; lowercase.

Exit code: 0 in all cases. `--dry-run` without `--refresh` errors with `"--dry-run requires --refresh"`, exit 2.

### 5.11 Doctor output (six blocks; verbatim)

The doctor adds two check lines (templates current; per-topic CLAUDE.md current) immediately before its existing `hook installed` row, OR replaces that placeholder row — implementer chooses the cleanest integration; the strings below are the contract.

**All-clear**:
```
ok    claude templates current (12 file(s) match bundle)
ok    per-topic CLAUDE.md current for all 4 topic(s)
```

**Stale templates only**:
```
warn  claude templates: 2 of 12 file(s) stale (older template version)
      run `remory init --refresh --dry-run` to inspect
ok    per-topic CLAUDE.md current for all 4 topic(s)
```

**User-edited stamped template**:
```
warn  claude templates: 1 file(s) edited after stamping (agents/extractor.md)
      run `remory init --refresh --dry-run` to inspect; `--refresh --force` to overwrite (.bak saved)
ok    per-topic CLAUDE.md current for all 4 topic(s)
```

**Per-topic CLAUDE.md stale in some topics**:
```
ok    claude templates current (12 file(s) match bundle)
warn  per-topic CLAUDE.md: 1 of 4 topic(s) stale (workout)
      run `remory init --refresh --dry-run` to inspect
```

**Settings missing**:
```
fail  .claude/settings.json missing — run `remory init` to recreate
```

**Settings malformed**:
```
fail  .claude/settings.json malformed: <one-line error>
      run `remory init --refresh --force` to recreate (.bak saved)
```

Format: status code (`ok` / `warn` / `fail`), four spaces, message. Remediation lines indented six spaces, no prefix. Matches existing doctor row style.

### 5.12 CHANGELOG entries (verbatim, under `## [Unreleased]`)

```markdown
### Changed
- `remory init` — first-run wizard now runs as a Claude Code subagent
  driven by the model; requires `claude` installed and logged in
  (points at `remory doctor` if not). See ADR-0006.
- `remory doctor` — reports template and per-topic `CLAUDE.md` drift
  as warnings; fails only on missing or malformed `.claude/settings.json`.

### Added
- `remory init --refresh [--force] [--dry-run]` — re-installs bundled
  `.claude/` templates and regenerates per-topic `CLAUDE.md`. Preserves
  user-edited files; writes `.bak` before any overwrite.
- SessionEnd hook installed: captures transcripts as raw entries when
  you talk to `claude` directly (outside `remory chat`). See ADR-0002.
- PreToolUse hook installed: refuses any attempt to edit `state.md`
  from within `claude` — the only legitimate writer is `remory sleep`.
```

### 5.13 `INSTRUCTIONS.md` §11 rewrite (replaces current §11)

```markdown
## 11. The `remory init` wizard — the fun bit

This is where the product earns its "warm and a little addictive" feel. The wizard is a single Claude Code session driven by the `wizard` subagent. The Python harness handles three things only: making sure the session can actually run, validating what comes back, and writing files atomically when the session is done.

### Preconditions

The wizard hard-requires `claude` on PATH and authenticated. If either fails the wizard refuses to launch and points the user at `remory doctor`. There is no offline fallback — the wizard's voice is the model's voice, and the model is the only voice that can do this step justice. No silent degradation.

### Flow

The harness:

1. Verifies the `claude` binary and authentication via the same probes `remory doctor` uses. On failure, prints a single line pointing at `remory doctor` and exits non-zero. No files written.
2. Materialises the bundled `.claude/` tree into the data directory if it has not been installed yet (idempotent first-time copy of `wizard.md`, the sleep subagents, slash commands, and `settings.json`).
3. Stages a tempdir as the session's *run directory*, populates it with `manifest.json` and a `schemas/` directory containing the built-in topic schemas, then tells the wizard subagent (via its system prompt) where to find them.
4. Launches `claude --agent wizard` with `cwd` set to the **data directory root**, not a topic directory. The cwd choice is load-bearing: the SessionEnd hook detects topic membership by cwd, so launching at the data-dir root keeps the wizard's transcript from being captured as a raw entry in any topic's `raw/`.

The `wizard` subagent runs the conversation entirely. Six beats: greet by name, pick topics, run each chosen topic's `wizard_questions`, ask one cross-cutting wish question, write `answers.json` and `letter.md` to the run directory, say a short closing line. Tone is warm and a little playful; one question at a time; no menus. Skipping any question is fine — the schema's defaults carry the fallback values.

After the session exits cleanly the harness:

5. Validates `answers.json` against a Pydantic model (versioned `version: 1` wire format). If validation fails, the harness relaunches the subagent once with the validation error embedded in the resume prompt. A second failure dumps the raw output (whatever was written) to `<data_dir>/.remory/wizard-recovery/<timestamp>/` and aborts with a remediation pointer — nothing the user said disappears silently.
6. Runs the COMMIT block: per-topic-atomic file writes (`meta.yaml`, `state.md`, `CLAUDE.md`) and the data-dir `about-me.md`. The COMMIT block is Python, runs under a deferred-SIGINT guard, and is the only writer of user-visible files. The per-topic-atomic contract is preserved verbatim from the prior implementation.
7. Prints a short outro: where the data lives, the chosen topics, and "try `remory chat <topic>` whenever you're ready".

The whole flow takes 2–4 minutes. Ctrl+C during the claude session ends it cleanly (claude handles the signal; the harness sees a non-zero exit and never enters COMMIT). Ctrl+C during COMMIT finishes the in-flight file and stops, surfacing partial state through `remory doctor` per the per-topic-atomic contract.
```

### 5.14 `INSTRUCTIONS.md` §10 SessionEnd bullet edit (user-approved verbatim)

In §10, the SessionEnd bullet currently reads:

> **`SessionEnd`** hook: invokes a small Python helper that reads the JSONL transcript, normalises it to markdown, writes it as a new raw entry, and bumps `pending_count`. Prints the friendly threshold suggestion if appropriate.

Replace the entire bullet with:

> **`SessionEnd`** hook: invokes a small Python helper that reads the JSONL transcript, normalises it to markdown, writes it as a new raw entry, and bumps `pending_count`. It does not print the threshold nudge. The nudge is owned by `remory chat` on session exit; users invoking `claude` directly will see the nudge on their next `remory chat`. See ADR-0002.

## 6. Public APIs

### 6.1 `wizard/_answers.py`

```python
class WizardKnobs(BaseModel):
    """Per-topic tone and strictness, drawn from the schema's Literal sets."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    tone: Literal["warm", "balanced", "direct"]
    strictness: Literal["gentle", "balanced", "rigorous"]

class WizardAnswers(BaseModel):
    """Wire-format answer surface written by the wizard subagent.

    version is the forward-compat hook; bumping it requires a migration
    plan analogous to RawStatus."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1]
    name: str | None
    chosen_topics: tuple[str, ...]
    knobs_by_topic: dict[str, WizardKnobs]
    wish: str | None
```

### 6.2 `wizard/_subagent.py`

```python
@dataclass(frozen=True)
class SubagentRunResult:
    answers: WizardAnswers
    letter: str

def stage_run_dir(run_dir: Path) -> None:
    """Materialise schemas/ and manifest.json under run_dir.
    Read built-in schemas via importlib.resources; write each as
    <run_dir>/schemas/<name>.yaml. manifest.json is a JSON list of
    schema names in lex order."""

def parse_run_dir(run_dir: Path) -> SubagentRunResult:
    """Read answers.json + letter.md from run_dir. Validate against
    WizardAnswers. Raises WizardAnswerParseError on any failure
    (missing file, invalid JSON, validation error). Returns
    SubagentRunResult on success."""

def dump_recovery(
    data_dir: Path,
    run_dir: Path,
    exc: WizardAnswerParseError,
) -> Path:
    """Write a recovery directory under <data_dir>/.remory/wizard-recovery/<iso-ts>/
    containing whatever the subagent produced (answers.json.malformed,
    letter.md if present) plus validation-error.txt. Returns the recovery
    dir path. Atomic per-file via atomic_write_bytes."""
```

### 6.3 `wizard/_orchestrator.py`

```python
def run_wizard(
    *,
    backend_factory: Callable[[], Backend] | None = None,
    console: Console | None = None,
    data_dir: Path | None = None,
) -> None:
    """Drive the claude-driven wizard flow:

    1. Preflight: check claude binary + auth via doctor's probes.
       Raise WizardPreflightError on failure.
    2. install_data_dir_templates(eff_data_dir, force=False).
    3. Stage run_dir tempdir with schemas + manifest.
    4. backend.chat(cwd=eff_data_dir, agent="wizard", resume=False).
    5. Validate answers.json + letter.md. On parse fail: one repair
       round with error embedded, then dump_recovery + hard fail.
    6. commit(answers, letter, data_dir=eff_data_dir).
    7. Print outro.

    input_fn is removed (no Python prompting). Signature otherwise
    stable from Phase 5.
    """
```

### 6.4 `claude_assets.py`

```python
PRODUCTION_TEMPLATE_VERSION: Final[int] = 1
TEMPLATE_VERSION_KEY: Final[str] = "_remory_template_version"

def stamp_markdown(body: str, *, version: int = PRODUCTION_TEMPLATE_VERSION) -> str:
    """Prepend the HTML-comment version stamp. Idempotent: re-stamping
    replaces an existing head-stamp; raises if a mismatched stamp appears
    mid-document (defensive)."""

def detect_version(body: str) -> int | None:
    """Return the integer template version from the head-stamp comment,
    or None if absent or unparseable."""

def install_data_dir_templates(
    data_dir: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> EmitResult:
    """Idempotently materialise <data_dir>/.claude/ from bundled templates.

    Policy:
    - Missing file → write (with stamp).
    - Stamp older than bundle → overwrite + .bak.
    - Stamp matches bundle, bytes match → skip (unchanged).
    - Stamp matches bundle, bytes differ → conflict; refuse unless force.
    - No stamp → preserve (likely user-authored); force does NOT override.
    - Stamp newer than bundle → warn but skip (downgrade-without-bump
      footgun; ADR-0005).

    dry_run reports what would happen without writing. Uses
    atomic_write_bytes for every write."""

def refresh(
    data_dir: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> EmitResult:
    """Call install_data_dir_templates AND regen_all_topic_claude_md;
    combine results. dry_run is propagated to both."""

def emit_backup(target_path: Path, data_dir: Path) -> Path:
    """Write a .bak of target_path under <data_dir>/.claude/.backups/.
    Path layout: <flattened-relative-path>.<UTC-iso-timestamp>.bak.
    Atomic via atomic_write_bytes. Returns the .bak path."""
```

### 6.5 `topic_claude_md.py`

```python
@dataclass(frozen=True)
class TopicClaudeMdContext:
    schema_name: str
    persona: str
    tone: Literal["warm", "balanced", "direct"]
    strictness: Literal["gentle", "balanced", "rigorous"]

def render(ctx: TopicClaudeMdContext) -> str:
    """Render the per-topic CLAUDE.md bytes. Pure. Byte-stable for
    fixed inputs. Stamped with PRODUCTION_TEMPLATE_VERSION. Uses the
    tone/strictness dispatch tables from §5.7."""

def regenerate_if_stale(
    topic_dir: Path,
    *,
    topic: Topic,
    force: bool = False,
    dry_run: bool = False,
) -> EmitEntry | None:
    """Re-render the topic's CLAUDE.md and compare. Write if missing,
    stamp-older, or re-rendered bytes differ from on-disk. Conflict
    handling identical to install_data_dir_templates (refuse on
    stamped-but-edited unless force; .bak on overwrite). Acquires
    topic lock timeout=0.0; caller must not already hold it.

    Returns an EmitEntry describing the action (or None if no change)."""

def regen_all_topic_claude_md(
    data_dir: Path,
    *,
    force: bool,
    dry_run: bool,
) -> tuple[EmitEntry, ...]:
    """Iterate every topic under <data_dir>/topics/. Skip topics with
    malformed meta.yaml (one EmitEntry per topic with reason=
    'meta-malformed'; does NOT abort the whole refresh). Returns one
    EmitEntry per topic."""
```

### 6.6 `hooks/session_end.py`

```python
@dataclass(frozen=True)
class SessionEndInput:
    cwd: Path
    session_id: str | None
    transcript_path: Path | None

@dataclass(frozen=True)
class SessionEndOutcome:
    status: Literal["wrote", "deferred_locked", "duplicate_skip",
                    "no_topic", "empty_transcript", "error"]
    raw_path: Path | None
    note: str

def run(payload: SessionEndInput) -> SessionEndOutcome:
    """Pure helper, idempotent. Steps:
    1. Resolve topic_dir from cwd; if not under data_dir/topics/<name>/,
       return no_topic.
    2. is_locked(topic_dir) → deferred_locked.
    3. topic_lock(timeout=0.0); on race, deferred_locked.
    4. Under lock: scan list_raw for matching session_id → duplicate_skip.
    5. transcripts.to_markdown(); if empty → empty_transcript + WARNING log.
    6. write_raw + bump meta.yaml pending_count + last_chat → wrote.
    Never prints the threshold nudge (D1).
    On exception: return error; do not raise (main() exits 0 always)."""

def main(argv: list[str] | None = None, stdin: io.TextIOBase | None = None) -> int:
    """Thin shim. Parses argv + stdin JSON payload (per claude's hook
    protocol). Builds SessionEndInput. Calls run(). Exits 0 ALWAYS."""
```

### 6.7 `hooks/pre_tool_use.py`

```python
@dataclass(frozen=True)
class PreToolUseInput:
    tool_name: str
    target_path: Path | None

@dataclass(frozen=True)
class PreToolUseDecision:
    allowed: bool
    message: str

def decide(payload: PreToolUseInput) -> PreToolUseDecision:
    """Allow iff tool is not Edit/Write, or target_path resolves to
    something that is NOT exactly <data_dir>/topics/<name>/state.md
    for some direct topic child. Symlinks resolved before matching.
    Basename-only matching is rejected."""

def main(argv: list[str] | None = None, stdin: io.TextIOBase | None = None) -> int:
    """Thin shim. Reads tool-input JSON from stdin. Calls decide().
    Returns claude's allow/deny response on stdout. Exit 0 on allow,
    non-zero on deny (claude's hook contract)."""
```

### 6.8 `backends/base.py`

```python
class Backend(Protocol):
    def chat(
        self,
        *,
        cwd: Path,
        resume: bool = False,
        agent: str | None = None,
    ) -> ChatResult: ...
```

`agent` is `None` for the chat_cmd path (preserves Phase 4 behaviour) and `"wizard"` for the wizard launch. Real claude CLI flag: `--agent <name>`.

## 7. Wizard orchestrator flow (pseudocode for the implementer)

```python
def run_wizard(*, backend_factory=None, console=None, data_dir=None):
    eff_data_dir = data_dir or _resolve_data_dir()
    eff_backend = (backend_factory or _default_backend_factory)()
    eff_console = console or make_console()

    # PRE-FLIGHT — reuse doctor's probes; do NOT reimplement.
    preflight = _preflight_claude_or_doctor(eff_backend)
    if not preflight.ok:
        sys.stderr.write(S.PRECONDITION_NEEDS_DOCTOR_MESSAGE)
        raise WizardPreflightError(preflight.reason)

    install_data_dir_templates(eff_data_dir, force=False)

    with TemporaryDirectory() as run_dir_str:
        run_dir = Path(run_dir_str)
        stage_run_dir(run_dir)

        # Launch interactive wizard subagent. cwd=eff_data_dir is
        # LOAD-BEARING (D4). Do not change without re-reading ADR-0002.
        try:
            result = eff_backend.chat(cwd=eff_data_dir, agent="wizard", resume=False)
        except KeyboardInterrupt:
            sys.stderr.write(S.PRE_COMMIT_INTERRUPT_MESSAGE)
            raise

        if result.exit_code != 0:
            sys.stderr.write(S.PRE_COMMIT_INTERRUPT_MESSAGE)
            raise WizardSubagentFailedError(...)

        try:
            run_result = parse_run_dir(run_dir)
        except WizardAnswerParseError as exc1:
            # One repair round with error embedded.
            _stage_repair_prompt(run_dir, exc1.message)
            try:
                result2 = eff_backend.chat(cwd=eff_data_dir, agent="wizard", resume=True)
            except KeyboardInterrupt:
                dump_recovery(eff_data_dir, run_dir, exc1)
                sys.stderr.write(S.PRE_COMMIT_INTERRUPT_MESSAGE)
                raise
            if result2.exit_code != 0:
                dump_recovery(eff_data_dir, run_dir, exc1)
                raise WizardSubagentFailedError(...)
            try:
                run_result = parse_run_dir(run_dir)
            except WizardAnswerParseError as exc2:
                recovery_dir = dump_recovery(eff_data_dir, run_dir, exc2)
                sys.stderr.write(S.RECOVERY_MESSAGE_TEMPLATE.format(recovery_dir=recovery_dir))
                raise

    commit(run_result.answers, run_result.letter, data_dir=eff_data_dir)
    _print_outro(eff_console, eff_data_dir, run_result.answers)
```

Resume semantics on the repair round: **implement the `--agent wizard --resume` path.** Mark the call site with an explicit TODO:

```python
# TODO(phase-6-smoke): if --resume drops the agent, switch to a fresh
# launch with the validation error as the leading prompt. The PR-
# description smoke checkbox §14 determines the outcome — either this
# TODO converts to a code change, or it gets deleted. The implementer
# does NOT choose at write time.
```

Behaviour-equivalent for tests either way (the stub Backend in unit tests does not exercise resume semantics).

## 8. Hook semantics — full table

### 8.1 SessionEnd

| cwd not under topics_root | → `no_topic`; exit 0 silently. Wizard-transcript skip relies on this (D4). |
| is_locked → True          | → `deferred_locked`; DEBUG log; exit 0. chat_cmd owns the write (ADR-0002). |
| matching session_id in raw | → `duplicate_skip`; DEBUG log; exit 0. |
| transcript renders empty   | → `empty_transcript`; **WARNING** log; exit 0. No silent data loss. |
| write succeeds              | → `wrote`; INFO log; exit 0. NEVER print threshold nudge (D1). |
| any exception              | → `error`; WARNING log (exception_type, topic, session_id only — no transcript echo per `feedback_log_omit_prompt_adjacent_fields`); exit 0. |

The `main()` shim exits 0 ALWAYS, regardless of `run()`'s status. Hooks must never block claude.

### 8.2 PreToolUse

| tool ∉ {Edit, Write} | → allow |
| target_path is None  | → allow (no path to match) |
| resolved target ≠ `<data_dir>/topics/<name>/state.md` for any direct topic | → allow |
| resolved target == `<data_dir>/topics/<name>/state.md` | → deny with §5.8 message |

Symlinks are resolved before matching. Basename-only matching is rejected (test pins this).

## 9. `--refresh` policy — full table

|                              | default `--refresh` | `--refresh --force` |
|------------------------------|---------------------|----------------------|
| Missing file                 | write + stamp       | write + stamp        |
| Stamp older than bundle      | overwrite + .bak    | overwrite + .bak     |
| Stamp matches, bytes match   | skip (unchanged)    | skip (unchanged)     |
| Stamp matches, bytes differ  | **conflict; skip**  | overwrite + .bak     |
| No stamp                     | preserve            | preserve (D5)        |
| Stamp newer than bundle      | warn + skip         | warn + skip          |

`--dry-run` exits 0 in all states. `--dry-run` without `--refresh` errors `"--dry-run requires --refresh"`, exit 2. The `--force` + unstamped case is the deliberate asymmetry (D5).

## 10. Doctor checks — full table

| Check                                            | Status    | Remediation pointer (if any) |
|--------------------------------------------------|-----------|-------------------------------|
| Every bundled template byte-matches on-disk      | ok        | —                             |
| Stamp-older templates exist                      | warn      | `remory init --refresh --dry-run` |
| Stamped-but-edited templates exist               | warn      | `remory init --refresh --dry-run`; `--refresh --force` to overwrite |
| All per-topic CLAUDE.md byte-match render        | ok        | —                             |
| Some per-topic CLAUDE.md stale                   | warn      | `remory init --refresh --dry-run` |
| `.claude/settings.json` missing                  | **fail**  | `remory init` to recreate     |
| `.claude/settings.json` malformed                | **fail**  | `remory init --refresh --force` to recreate (.bak saved) |

Doctor reports per-topic drift as a single summary line, not one line per topic.

## 11. Test surface (named tests; ~80 total)

Names per `feedback_test_names_encode_contract.md`: encode function + precondition + outcome.

### 11.1 Unit tests

**`tests/unit/test_wizard_answers_model.py`** (NEW):
- `test_wizard_answers_round_trips_through_json_when_well_formed`
- `test_wizard_answers_rejects_unknown_tone_value`
- `test_wizard_answers_rejects_unknown_strictness_value`
- `test_wizard_answers_rejects_extra_top_level_key`
- `test_wizard_answers_rejects_version_other_than_1`
- `test_wizard_answers_allows_null_name_and_null_wish`
- `test_wizard_answers_rejects_knobs_for_unchosen_topic`

**`tests/unit/test_wizard_subagent_handoff.py`** (NEW):
- `test_parse_run_dir_returns_answers_and_letter_when_both_files_valid`
- `test_parse_run_dir_raises_when_answers_json_missing`
- `test_parse_run_dir_raises_when_letter_md_missing`
- `test_parse_run_dir_raises_when_answers_json_malformed_json`
- `test_parse_run_dir_raises_when_answers_json_validation_fails`
- `test_dump_recovery_writes_malformed_and_validation_error_when_both_present`
- `test_dump_recovery_omits_letter_when_absent`

**`tests/unit/test_wizard_orchestrator.py`** (REWRITE):
- `test_run_wizard_skips_subagent_and_raises_when_preflight_fails`
- `test_run_wizard_commits_when_subagent_writes_valid_files`
- `test_run_wizard_retries_once_when_first_answers_malformed_then_commits`
- `test_run_wizard_dumps_recovery_and_raises_when_second_attempt_fails`
- `test_run_wizard_does_not_enter_commit_when_subagent_exits_nonzero`
- `test_run_wizard_passes_data_dir_through_to_commit_unchanged`

Uses a stub `Backend` (not `fake_claude`).

**`tests/unit/test_claude_assets_install.py`** (NEW):
- `test_install_data_dir_templates_first_time_writes_all_and_stamps`
- `test_install_data_dir_templates_idempotent_when_stamps_match`
- `test_install_data_dir_templates_skips_unstamped_user_modified_file_and_returns_in_skipped`
- `test_install_data_dir_templates_overwrites_when_stamp_is_older_and_writes_bak`
- `test_install_data_dir_templates_uses_atomic_writes_per_file`

**`tests/unit/test_claude_assets_template_version.py`** (NEW):
- `test_stamp_markdown_prepends_idempotent_comment`
- `test_detect_version_returns_int_when_present`
- `test_detect_version_returns_none_when_absent`
- `test_detect_version_returns_none_for_garbage_stamp`

**`tests/unit/test_claude_assets_settings.py`** (NEW):
- `test_settings_json_bytes_byte_pinned` (snapshot test against §5.6)
- `test_settings_json_pins_template_version_key_and_value`
- `test_settings_json_session_end_command_uses_remory_hook_session_end`
- `test_settings_json_pre_tool_use_matcher_is_edit_pipe_write`

**`tests/unit/test_topic_claude_md.py`** (NEW):
- `test_render_byte_stable_for_workout_warm_balanced` (snapshot)
- `test_render_byte_stable_for_coaching_warm_gentle` (snapshot)
- `test_render_byte_stable_for_job_profile_warm_balanced` (snapshot)
- `test_render_includes_template_version_stamp`
- `test_render_tone_line_dispatch_table_covers_all_three_values`
- `test_render_strictness_line_dispatch_table_covers_all_three_values`
- `test_regenerate_if_stale_writes_when_file_missing`
- `test_regenerate_if_stale_writes_when_stamp_older`
- `test_regenerate_if_stale_writes_when_knobs_changed_in_meta`
- `test_regenerate_if_stale_skips_when_byte_identical`
- `test_regenerate_if_stale_acquires_topic_lock_timeout_zero`

**`tests/unit/test_emit_backup.py`** (NEW):
- `test_emit_backup_writes_atomic_under_dot_claude_backups`
- `test_emit_backup_path_uses_flattened_slashes`
- `test_emit_backup_path_uses_utc_iso_timestamp_with_colons_replaced`
- `test_emit_backup_creates_backups_dir_if_missing`

**`tests/unit/test_hook_session_end.py`** (NEW):
- `test_session_end_hook_returns_no_topic_when_cwd_not_under_topics_root`
- `test_session_end_hook_returns_no_topic_when_cwd_is_data_dir_root_not_topic_subdir` (Gap B; load-bearing for wizard-transcript skip)
- `test_session_end_hook_returns_deferred_locked_when_chat_parent_holds_lock`
- `test_session_end_hook_returns_duplicate_skip_when_session_id_already_recorded`
- `test_session_end_hook_writes_raw_entry_when_unlocked_and_no_duplicate`
- `test_session_end_hook_returns_empty_transcript_and_logs_warning_when_markdown_empty`
- `test_session_end_hook_returns_error_without_raising_when_meta_yaml_unparseable`
- `test_session_end_hook_logs_omit_transcript_bodies_and_stderr_tail`
- `test_session_end_hook_bumps_pending_count_and_last_chat_on_write`
- `test_session_end_hook_main_exits_zero_always_even_on_error`
- `test_session_end_hook_never_prints_threshold_nudge_when_pending_crosses_threshold` (pin D1)

**`tests/unit/test_hook_pre_tool_use.py`** (NEW):
- `test_pre_tool_use_decide_allows_unrelated_tool_invocation`
- `test_pre_tool_use_decide_allows_edit_to_non_state_md_file_in_topic`
- `test_pre_tool_use_decide_allows_edit_to_state_md_outside_topics_tree`
- `test_pre_tool_use_decide_blocks_edit_to_topic_state_md`
- `test_pre_tool_use_decide_blocks_write_to_topic_state_md`
- `test_pre_tool_use_decide_block_message_is_user_facing_string` (pin §5.8)
- `test_pre_tool_use_decide_resolves_symlinks_before_matching`

**`tests/unit/test_chat_cmd.py`** (EXTEND):
- `test_chat_threshold_nudge_only_fires_in_chat_cmd_not_hook` (pin D1)

**`tests/unit/test_init_refresh.py`** (NEW):
- `test_init_refresh_writes_all_templates_when_data_dir_clean`
- `test_init_refresh_skips_stamped_but_edited_file_and_returns_conflict`
- `test_init_refresh_force_overwrites_stamped_but_edited_and_writes_bak`
- `test_init_refresh_writes_bak_for_stamped_older_overwrite_without_force`
- `test_init_refresh_preserves_unstamped_file_even_with_force` (pin D5)
- `test_init_refresh_treats_on_disk_version_greater_than_bundle_as_warn_not_overwrite`
- `test_init_refresh_continues_when_one_topic_has_malformed_meta_yaml_and_reports_skip`
- `test_init_refresh_regenerates_per_topic_claude_md_when_knobs_changed_in_meta`
- `test_init_refresh_regenerates_per_topic_claude_md_when_template_version_older`
- `test_init_refresh_skips_user_edited_per_topic_claude_md_without_force`
- `test_init_refresh_does_not_touch_state_md_or_meta_yaml_or_raw_dir`
- `test_init_refresh_dry_run_writes_nothing_when_changes_pending`
- `test_init_refresh_dry_run_writes_nothing_when_nothing_to_change`
- `test_init_refresh_dry_run_lists_each_category_correctly`
- `test_init_refresh_dry_run_exits_zero_in_all_states`
- `test_init_dry_run_without_refresh_errors`

**`tests/unit/test_doctor_claude_assets.py`** (NEW):
- `test_doctor_reports_ok_when_every_bundled_template_byte_matches_disk`
- `test_doctor_warns_when_stamped_template_edited_on_disk_and_points_at_dry_run`
- `test_doctor_warns_when_one_topic_claude_md_stale_and_names_count_not_all_topics`
- `test_doctor_fails_when_settings_json_missing`
- `test_doctor_fails_when_settings_json_malformed_and_remediation_mentions_force`
- `test_doctor_summary_line_for_topics_is_single_line_regardless_of_topic_count`

**`tests/unit/test_wizard_commit.py`** (TOUCH): update fixtures to construct `WizardAnswers` as Pydantic. Names unchanged.

**`tests/unit/test_wizard_sigint.py`** (TOUCH): drop pre-COMMIT SIGINT cases that depended on Python steps; keep mid-COMMIT (still operative). Add `test_run_wizard_does_not_enter_commit_when_subagent_killed_by_sigint`.

**`tests/unit/test_cli_errors.py`** (EXTEND):
- `test_format_error_renders_wizard_preflight_error_with_doctor_pointer`
- `test_format_error_renders_wizard_subagent_failed_with_recovery_dir`

**`tests/unit/test_wizard_messages.py`** (RENAMED from `test_wizard_strings.py`): covers only the kept entries from §5.9 plus the two new strings.

### 11.2 Integration tests

**`tests/integration/test_wizard_e2e.py`** (REWRITE):
- `test_wizard_e2e_writes_topic_dirs_and_about_me_when_fake_claude_produces_valid_json`
- `test_wizard_e2e_retries_once_and_succeeds_when_fake_claude_first_writes_malformed_then_valid`
- `test_wizard_e2e_writes_recovery_and_exits_nonzero_when_fake_claude_malformed_twice`
- `test_wizard_e2e_refuses_to_run_when_preflight_fails`
- `test_wizard_e2e_leaves_no_files_when_user_kills_subagent`

Uses `fake_claude` `wizard_interactive` mode.

**`tests/integration/test_hooks_against_fake_claude.py`** (NEW):
- `test_chat_writes_raw_and_session_end_hook_skips_when_remory_chat_owns_the_lock`
- `test_pretool_hook_blocks_claude_from_editing_state_md_during_chat`

**`tests/integration/test_session_end_hook_e2e.py`** (NEW):
- `test_session_end_hook_writes_raw_entry_when_chat_parent_missing`
- `test_session_end_hook_threshold_nudge_is_not_printed_by_hook` (pin D1)
- `test_session_end_hook_uses_to_markdown_renderer_not_its_own`

**`tests/integration/test_chat_vs_session_end_dedup.py`** (NEW):
- `test_chat_canonical_writes_raw_entry_under_lock`
- `test_session_end_hook_defers_when_chat_parent_still_holds_lock`
- `test_session_end_hook_skips_duplicate_when_session_id_already_on_disk`
- `test_no_double_raw_entry_when_chat_and_hook_both_fire`

**`tests/integration/test_doctor_e2e.py`** (EXTEND):
- `test_doctor_reports_dot_claude_present_when_init_refresh_has_run`

### 11.3 Snapshot / byte tests

**`tests/unit/test_data_templates_snapshot.py`** (NEW): byte-pin each bundled file against `src/remory/data_templates/.claude/...` contents. One snapshot test per file (`wizard.md`, `extractor.md`, `merger.md`, `critic.md`, four slash commands, `settings.json`).

## 12. `fake_claude` `wizard_interactive` mode

Add a single new mode to `tests/fakes/fake_claude` (extension, not a new file):

- `FAKE_CLAUDE_MODE=wizard_interactive`
- `FAKE_CLAUDE_WIZARD_RUN_DIR` env: where to write `answers.json` + `letter.md`.
- `FAKE_CLAUDE_WIZARD_ANSWERS` env: JSON string to write to `answers.json`.
- `FAKE_CLAUDE_WIZARD_LETTER` env: string to write to `letter.md`.
- Exit 0 after writing both.

Failure variants (set `FAKE_CLAUDE_WIZARD_FAIL`):
- `preflight_exit_nonzero` — exit 1 without writing.
- `write_malformed_json_once` — malformed first invocation, valid second.
- `write_malformed_json_twice` — malformed both invocations.
- `missing_answers_file` — write only `letter.md`.

Counter file mechanism (already in `fake_claude`) tracks first vs second invocation for the repair-loop tests.

## 13. PR description structure

Single PR. The PR description body uses this five-section split (reviewer-pass order):

```markdown
## Phase 6: Claude Code subagents and hooks

(short summary)

### Section 1 — Template emitter + backups (ADR-0005)
- `data_templates/`, `claude_assets.py`, `emit_backup`
- Stamp policy, `.bak` layout, atomic writes

### Section 2 — Wizard rearchitecture (ADR-0006)
- `wizard/_orchestrator.py` rewrite, `_answers.py` Pydantic-ification, `_subagent.py`
- `wizard.md` subagent template
- `fake_claude` `wizard_interactive` mode
- Phase 5 graveyard cleanup: deleted `_steps.py`, `_letter.py`, `_validators.py` + their tests

### Section 3 — Hooks (ADR-0007, refs ADR-0002)
- `hooks/session_end.py`, `hooks/pre_tool_use.py`
- `remory _hook` Typer subapp
- `chat_cmd` unchanged

### Section 4 — Doctor revisions
- Template + per-topic CLAUDE.md confirmation checks
- New strings per §5.11

### Section 5 — CLI + spec edits
- `remory init --refresh [--force] [--dry-run]`
- `INSTRUCTIONS.md` §10 edit, §11 rewrite
- CHANGELOG entries (§5.12)

### Deferred real-CLI smoke tests
- [ ] `claude --agent wizard` runs interactively without error on a clean data dir.
- [ ] A captured-stdin sample from a real `claude` SessionEnd invocation matches the hook parser's expected payload shape.
```

## 14. ADRs to ship in this PR

- `docs/adr/0005-claude-template-backups-retention.md` — `.bak` layout, no cleanup in v0.1, future `remory clean-backups` deferred.
- `docs/adr/0006-wizard-claude-driven-interview.md` — rationale (spec §11 literal reading + UX), JSON wire surface, one-retry repair, recovery dir, no offline fallback, cwd-based wizard-transcript skip cross-references ADR-0002.
- `docs/adr/0007-session-end-hook-never-prints.md` — chat-only nudge ownership; references ADR-0002.
- Amend `docs/adr/0002-chat-vs-session-end-hook-raw-write-coordination.md` with a one-line note: "The wizard launches `claude --agent wizard` with `cwd=eff_data_dir`, NOT a topic dir. The SessionEnd hook's `no_topic` branch is therefore the wizard-transcript skip mechanism. Do not move the wizard launch dir without re-reading this ADR."

## 15. Done = all of the following

1. `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest` — all green.
2. Three new ADRs exist (0005, 0006, 0007); ADR-0002 amended.
3. `INSTRUCTIONS.md` §10 edit (per §5.14) and §11 rewrite (per §5.13) applied.
4. `CHANGELOG.md` has the three entries from §5.12 under `## [Unreleased]`.
5. PR description body has the §13 five-section split + two deferred-smoke checkboxes.
6. Suppressions (`# noqa`, `# type: ignore`, `pytest.skip`) are permitted **with an inline comment naming the reason** (third-party stubs, tempfile edge cases, etc. are all fair game). What is forbidden is unexplained suppression. Implementer does not have to fight the type checker by restructuring real code to avoid a justified one-line suppression.
7. Phase 5 graveyard files deleted; no broken imports; no orphaned tests; renamed `test_wizard_strings.py → test_wizard_messages.py` applied.
8. Single PR. Will be large — that's understood. Reviewer-pass split is in the body.

## 16. Escalation rule (RESTATED)

Three architect passes is enough. If the implementer finds a fourth gap, stop and surface. Watch for:

- A "concrete string" needs parametrisation the plan didn't anticipate.
- A test in the planned ~50 can't be written without a fixture the plan didn't specify.
- A conflict between Phase 5 graveyard cleanup and the rewritten orchestrator that wasn't visible at architect time.

In all three cases: write a short report describing the gap and the specific blocking line, hand back. Do not paper over.

For everything else (typos, naming choices not pinned, ordering of independent edits): exercise judgement. The escalation rule is for *contract violations*, not plan-silent micro-decisions.
