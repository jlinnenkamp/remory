# Phase 4 — CLI surface (binding consolidated plan)

This document is the binding contract for Phase 4 implementation. It consolidates the architect plan with all user-applied refinements through ten rounds of review. Implementer must follow this verbatim. Surface any new ambiguity narrowly with a recommended default rather than improvising.

Spec anchors: `INSTRUCTIONS.md` §6, §10, §11, §14. Memory notes: `project_phase4_doctor_warns_noncanonical_frontmatter`, `project_phase4_doctor_owns_auth_policy`, `project_phase4_chat_vs_sessionend_coordination`, `feedback_ux_phase_concrete_strings`, `feedback_test_names_encode_contract`, `feedback_silently_means_logged`, `feedback_no_silent_data_loss`, `feedback_serialization_format_choice`, `feedback_commit_message_one_draft`, `feedback_reviewer_nit_default`.

## 1. User-confirmed decisions

**D1. Chat lock mechanics (CC2 refined).** Fork+wait, not release/re-acquire. Python parent acquires `topic_lock(topic_dir, timeout=0.0)` once at chat start; runs `claude` as a subprocess that does *not* hold the lock; on subprocess exit the parent (still holding the lock) writes the raw entry and releases. No 10s grace window. Captured in **ADR 0001**.

**D2. SessionEnd hook coordination — policy (b).** Chat parent is canonical writer when `remory chat` is the invocation. Phase 6 hook defers via `locking.is_locked(topic_dir)` non-blocking probe (skip silently if held) plus `list_raw` session-id duplicate scan as belt-and-suspenders correctness floor. `chat_cmd.py` ships once and does not change between Phase 4 and Phase 6 — all coordination logic lives in the Phase 6 hook script. Captured in **ADR 0001**.

**D3. Wizard data-dir confirmation (Q-W5).** Path inlined into the welcome banner; no Y/N prompt.

**D4. Doctor auth probe (Q-D1).** LLM round-trip only. No filesystem-archaeology of `~/.claude/...`.

**D5. Wizard COMMIT partial-failure policy (Q-W8).** Leave-as-is; surface partial state via doctor. Captured in **ADR 0002**.

**D6. Topic-state preconditions for chat (Q-CC7) — three cases:**
- **Topic doesn't exist** (no directory) → `Topic '<name>' doesn't exist yet.` / `Run remory init <name> to set it up. Existing topics: <comma-separated>` (or `Run remory init to set one up.` if zero topics). Exit 2.
- **Topic incomplete** (directory exists but `state.md` OR `meta.yaml` missing or unparseable) → `Topic '<name>' is in an incomplete state.` / `Run remory doctor to inspect — init could overwrite partial files.` Exit 2.
- **Topic complete** → chat proceeds.

**D7. `init` refusal on existing topic (Q-W7) — pinned wording:**

```
Topic '<name>' already exists at <path>. To re-run the wizard for it,
delete the topic directory first (`rm -rf <path>`) and run `remory init
<name>` again. To set up a different topic, run `remory init <other>`.
```

Exit 1.

**D8. Wizard skip-text shows the default (Q-W1/W2).** Each option-style question shows what skip resolves to inside the `[s]` line: `[s] Skip — use the default ("<label>")`. Workout strictness is special (see R1 below).

**D9. Read-aloud nits captured.**
- Welcome banner inlines path per D3.
- Outro is pluralize-aware (`topic:` for 1, `topics:` for 2+).
- Doctor footer count matches rows shown ("5 checks" not "3").
- `claude binary` version hidden by default; surfaced only when doctor needs to flag it (`--probe-real-cli` mismatch or version-related warns).
- "Sleep will retry 9 times before failing if you skip this" line in the auth-failure remediation locked exactly as written.

**D10. Forward-debt for v0.2 (Q-D2 + Q-CC5):** `--exit-on-warn` semantics for scripted users. Capture as v0.2 candidate; v0.1 ships warnings as informational with exit 0.

## 2. Seven refinements (binding, applied here)

**R1. Workout strictness skip text.** Replace `[s] Skip — use the default ("balanced; I won't pick for you")` with `[s] Skip — leave it at the default for now ("balanced")`. The previous wording editorialised defensively; the default isn't something the wizard needs to apologise for. "Leave it at the default for now" implies the user can change it later, which maps to the editable-knobs reality.

The pattern generalises: any `[s] Skip` line where the schema default doesn't match an offered option uses `Skip — leave it at the default for now ("<value>")`. For schemas where the default *does* match an offered option, keep `Skip — use the default ("<label>")`.

**R2. Init stub error wording (--schema missing).** Replace `remory init's interactive wizard ships in a follow-up release` with `The interactive wizard isn't built yet.`. "Ships in a follow-up release" is marketing-speak that jars against the wizard's warm tone.

Final wording for the init stub error when `--schema` is missing:

```
The interactive wizard isn't built yet. For now, pass --schema to pick a
built-in: --schema job-profile, --schema workout, or --schema coaching.
```

**R3. CritiqueError row in error mapping table.** Keep the row but add the parenthetical note `(orchestrator converts to SleepResult.warnings before reaching the CLI; this row exists as a contract reminder — if the CLI ever sees CritiqueError, that's a bug in the orchestrator)` to the User-message column. Preserves the contract reminder without confusing the policy.

**R4. SUCCESS_WITH_WARNINGS rendering — pin the literal text in a sleep-output rendering section (not just the error table).** The literal text:

```
note: critique step couldn't run; state.md is up to date but _review.md
wasn't refreshed.
```

This appears as an italic line at the end of `sleep` output when `SleepResult.status == SUCCESS_WITH_WARNINGS` and `_review.md` was not written. Document it in `ui.py::print_sleep_summary` (or wherever sleep output rendering lives) with a comment pinning the literal text. It is a sleep-output normal path, not an error path.

**R5. Auth-probe substring matching implementation pin.** Use `tail.lower()` once + substring check, not per-variant case branching. Concretely:

```python
auth_keywords = ("login", "unauthorized", "authenticate")
tail_lower = stderr_tail.lower()
if any(k in tail_lower for k in auth_keywords):
    # FAIL — auth-likely
    ...
```

Not:

```python
# DO NOT WRITE LIKE THIS
if "login" in stderr_tail.lower() or "Login" in stderr_tail or ...:
    ...
```

The `.lower()` once + `any(k in tail_lower for k in keywords)` form is mandatory. Pin in `_check_claude_auth` docstring + `test_check_claude_auth_*_case_insensitive_via_lower_once`.

**R6. Hook-installed check phrasing.** Replace `Phase 6 will install; raw entries are created by remory chat only until then` with `Phase 6 ships the hook; in v0.1, only remory chat creates raw entries — direct claude invocations don't produce a record.`. Names what users actually need to know: **direct `claude` invocations are not captured** in v0.1.

Final doctor INFO line:

```
  info hook installed    no   (Phase 6 ships the hook; in v0.1, only
                              `remory chat` creates raw entries — direct
                              claude invocations don't produce a record)
```

**R7. Confirmed: missing config.toml is OK, not INFO.** Users-by-design using defaults aren't a problem; INFO would add noise. The doctor table's `ConfigError → FAIL` policy stands; missing-file produces `ok   config           defaults (no config.toml found)`.

## 3. Wizard literal strings — end-to-end (Phase 5 implementation contract)

The wizard runs as a Phase 5 implementation against this binding text. Phase 4 ships `init` as a non-interactive stub; the wizard lands in Phase 5. **String changes in Phase 5 require ambiguity surfacing** — strings here are binding.

### 3.1 Welcome (path inlined per D3)

```
Remory.

A second brain that actually remembers — but only the bits you bring it.
Your data will live at: <resolved data_dir>

This first run takes about three minutes. Two short questions for each
topic you pick. You can skip any of them; I'll use a sensible default.

Press Ctrl+C any time. Nothing is written until the very end, and if
you stop partway, nothing is left behind.
```

Plain-text fallback when `not isatty()` or `COLUMNS < 60`: same lines, no centring.

### 3.2 Step 1 — Name

```
What should I call you?
> 
```

- **Validate:** 1–60 chars after `.strip()`; reject if contains a newline.
- **Re-prompt:** empty → `That came back blank — try again, or press Ctrl+C to bail.` / too long → `A bit long — let's keep it under 60 characters so I can fit it on a line.`
- **Skip:** `[skip]` accepted. About-me.md omits the name; wizard refers to user as "you" thereafter.

### 3.3 Step 2 — Pick topics (multi-select)

```
Which of these would you like to set up? (You can add more later.)

  [1] job-profile  — career direction; interviews and self-reflection
                     accumulate into an evolving picture.
  [2] workout      — a living plan plus session logs; adapts as you do.
  [3] coaching     — therapy and coaching insights, gathered without
                     pushing interpretations.

Pick one or more by number, separated by commas. Press Enter for all three.
> 
```

- **Validate:** comma- or space-separated integers in `{1,2,3}`. Empty = all three.
- **Re-prompt on invalid:** `That didn't parse — try something like "1,3" or just press Enter for all three.`
- **No skip;** at least one topic is required.
- **Mapping:** built from `schema.iter_builtin()` sorted lexicographically.

### 3.4 Step 3 — Per-topic preamble + questions (D8 + R1)

For each chosen topic, in selection order: preamble paragraph + the schema's two `wizard_questions`.

#### 3.4.1 job-profile

Preamble:

```
job-profile — career direction.

Two short questions and you're done. The point of this topic is to
notice what you actually want from work over time, not to give you
advice on the spot.
```

Q1 (tone):

```
When you say something contradictory across sessions, do you want me to
gently flag it, or pretend I didn't notice?

  [1] Gently flag, with care
  [2] Just call it out
  [s] Skip — use the default ("Gently flag, with care")

> 
```

Q2 (strictness):

```
How rigorous should I be when assessing a job option you bring up?

  [1] Encouraging
  [2] Stress-test it
  [s] Skip — use the default ("Encouraging")

> 
```

#### 3.4.2 workout

Preamble:

```
workout — your living training plan.

Two short questions and you're done. I won't program for you;
I'll just hold the plan and what you actually did, and notice
when those drift apart.
```

Q1 (tone):

```
When a session goes badly, do you want me warm about it, or do you
want me to just say what I see?

  [1] Warm; meet me where I am
  [2] Direct; just tell me
  [s] Skip — use the default ("Direct; just tell me")

> 
```

Q2 (strictness, R1 wording):

```
How strict should I be about programming and progression?

  [1] Lenient; life happens
  [2] Hold me to the plan
  [s] Skip — leave it at the default for now ("balanced")

> 
```

(Skip resolves to `balanced` per D8; schema default `balanced` doesn't match either offered option — the R1 wording acknowledges the editable-knobs reality without apologising.)

#### 3.4.3 coaching

Preamble:

```
coaching — a quiet place for what comes up in therapy or coaching.

Two short questions. I won't play therapist with you. I'll just
hold themes lightly, and not push interpretations you haven't
arrived at yourself.
```

Q1 (tone):

```
How do you want me to hold what you bring here — close and warm,
or measured and a bit cooler?

  [1] Close and warm
  [2] Measured and steady
  [s] Skip — use the default ("Close and warm")

> 
```

Q2 (strictness):

```
When you arrive at an insight, do you want me to test it or take it
as you offered it?

  [1] Take it as offered
  [2] Test it lightly
  [s] Skip — use the default ("Take it as offered")

> 
```

#### 3.4.4 Validation for every option-style question

- **Accepted:** `1`, `2`, `s`/`skip`/`Skip`/`S` (case-insensitive on letter forms; integer must be exactly the digit).
- **Rejected:** `0`, `3+`, alphabetic input that isn't `s`/`skip`, multi-character integers like `01`, multi-line paste.
- **Re-prompt on invalid:** `Sorry, I didn't follow — pick 1, 2, or s.`
- **Three consecutive invalid attempts** → bail with `Three tries — let's stop here. Run remory init again when you're ready.`, exit 2.

### 3.5 Step 4 — One-sentence wish

```
One last thing. In a sentence — what are you hoping a second brain
helps you do?

> 
```

- **Validate:** 1–500 chars after `.strip()`, no newlines.
- **Re-prompt:** empty → `Take a guess — even a half-sentence helps me get the tone right.` / too long → `Trim it a little — under 500 characters keeps it sentence-shaped.` / newline → `Single sentence, no line breaks, please.`
- **Skip:** `[skip]` (or empty after one re-prompt) accepted.

### 3.6 Step 5 — The letter (LLM call, optional)

Pre-call:

```
One moment — writing back what I heard.
```

On success:

```
I read back to you what I picked up just now —

  <model paragraph, indented two spaces, wrapped to terminal width>

That paragraph is the first line of your about-me.md. You can edit
it any time; I'll re-read it when we talk.
```

On backend failure: degrade to a hand-written fallback paragraph composed from the answers, prefixed with `(I couldn't reach the model just now, so this is a quick stand-in.)`. Subagent failure logged at WARNING; init does not fail.

### 3.7 Step 6 — Outro (pluralize-aware per D9)

For one topic:

```
You're set up.

  data dir:        <resolved data_dir>
  topic:           workout
  about-me.md:     <path>

Try `remory chat workout` whenever you're ready. When the conversation
feels done, run `remory sleep workout` to fold what you said into the
topic's memory.

If something looks off, `remory doctor` will tell you.
```

For two or more topics:

```
You're set up.

  data dir:        <resolved data_dir>
  topics:          job-profile, workout
  about-me.md:     <path>

Try `remory chat job-profile` whenever you're ready, or `remory chat
workout` to start there. When the conversation feels done, run
`remory sleep <topic>` to fold what you said into the topic's memory.

If something looks off, `remory doctor` will tell you.
```

The chat suggestion uses **the first topic the user picked** for the primary suggestion.

### 3.8 State machine + on-disk semantics

- **Linear, no back-navigation.** `welcome → name → pick_topics → [for each topic: preamble → q1 → q2] → wish → letter → outro → COMMIT`.
- **Pre-COMMIT:** zero disk writes. All answers in an in-memory `WizardAnswers` dataclass.
- **COMMIT:** atomic batch — `data_dir.mkdir`, `topics_dir().mkdir`, then per chosen topic acquire `topic_lock`, write `meta.yaml` + `state.md` skeleton + 3-line `CLAUDE.md` placeholder, release. Then write `about-me.md`. No `config.toml` write in v0.1.
- **COMMIT partial failure (D5):** leave-as-is. User-facing: `Stopped mid-write at topic '<name>'. Topic '<prior>' was created successfully. Run remory doctor to inspect, or remory init <name> to retry the failed topic.` Exit 1.
- **`Ctrl+C` mid-wizard:**
  - Pre-COMMIT → `Stopped. No files written. Run remory init when you're ready.`, exit 130.
  - During COMMIT → SIGINT-ignored until the in-flight atomic write completes. Final message: `Stopped mid-write. Some files may exist. Run remory doctor to inspect.`, exit 130.

### 3.9 init stub (Phase 4 ships this)

`remory init <topic-name> --schema <name>` non-interactive stub.

- **Without `--schema` (R2 wording):**
  ```
  The interactive wizard isn't built yet. For now, pass --schema to pick a
  built-in: --schema job-profile, --schema workout, or --schema coaching.
  ```
  Exit 2.
- **Schema typo:** validates against `schema.BUILTIN_NAMES`. On miss, `difflib.get_close_matches`:
  ```
  Unknown schema 'jobprofile'.
  
  Did you mean: job-profile?
  
  Available built-in schemas: coaching, job-profile, workout.
  ```
  Exit 2. Omit "Did you mean" line if no close match.
- **Topic exists already (D7):** the pinned 3-line wording. Exit 1.
- **Topic name validation:** `paths._validate_topic_name`. On failure, exit 2 with the regex hint.
- **Failure paths:** disk full, narrow terminal — same handling as the wizard would do.

The stub creates `data_dir`, `topics_dir`, `topic_dir` (acquiring `topic_lock`), writes `meta.yaml` (with schema defaults for knobs), `state.md` skeleton, 3-line `CLAUDE.md` placeholder. No `about-me.md` write (wizard-only).

## 4. Doctor output formats

Each check is one of `OK ✓`, `WARN !`, `FAIL ✗`, `SKIP ·`, `INFO i`. ASCII alternatives `ok`/`warn`/`fail`/`skip`/`info` (4-char right-padded labels) when `not isatty()` or `--no-color`.

### 4.1 Clean run (D9 reconcile + D9 hidden version)

```
remory doctor
================================================================

  ok   data_dir         /home/user/.local/share/remory
  ok   config           /home/user/.config/remory/config.toml
  ok   claude binary    /usr/local/bin/claude
  ok   claude auth      logged in as <account_email_or_id>
  ok   topics (3)       coaching, job-profile, workout

5 checks, 0 warnings, 0 failures. You're good.
```

Exit 0. `claude binary` version hidden per D9.

### 4.2 Mixed-failures run (locked verbatim where indicated)

```
remory doctor
================================================================

  ok   data_dir         /home/user/.local/share/remory
  ok   config           defaults (no config.toml found)
  ok   claude binary    /usr/local/bin/claude
  fail claude auth      not logged in
       -> run `claude` once interactively to log in, then re-run
          `remory doctor`. Sleep will retry 9 times before failing
          if you skip this.

  ok   topic: coaching            schema OK, 2 pending entries
  warn topic: job-profile         schema drift: 1 section in state.md
                                  is not in the schema ('# Notes').
       -> the next sleep will drop that section. Move the content
          into a schema section, or add 'notes' to the schema, before
          running `remory sleep job-profile`.
  fail topic: workout             stale .lock file (no holder)
       -> remove /home/user/.local/share/remory/topics/workout/.lock
          and re-run `remory doctor`.

7 checks, 1 warning, 2 failures. Fix the failures before sleeping.
```

Exit 1. The "Sleep will retry 9 times before failing if you skip this" line is **locked verbatim per D9**; do not edit during implementation.

### 4.3 `--strict` adds non-canonical state.md warn

```
  warn topic: coaching            state.md is hand-edited; the next
                                  sleep will canonicalise the YAML
                                  frontmatter (key order: schema,
                                  schema_version, last_consolidated,
                                  entries_consolidated; UTC datetimes
                                  rendered with 'Z' suffix).
       -> diff after a sleep: cp state.md state.md.before; remory
          sleep coaching; diff state.md.before state.md.
```

WARN, exit 0. Check runs only under `--strict`.

### 4.4 `--probe-real-cli` adds path-encoding probe

```
  ok   real-cli probe   path-encoded transcript matches our locator
                        (encoded as -home-user-tmp-pytest-xyz)
```

On mismatch:

```
  fail real-cli probe   our cwd-encoder produced 'foo-bar' but
                        claude wrote its transcript at 'foo--bar'.
       -> file an issue with this output and your claude version
          (v1.0.119).
```

Off by default; one extra LLM call when enabled.

### 4.5 Hook-installed status (R6 wording)

```
  info hook installed    no   (Phase 6 ships the hook; in v0.1, only
                              `remory chat` creates raw entries — direct
                              claude invocations don't produce a record)
```

Info-only. Phase 6 promotes it to actionable.

### 4.6 Full check list (execution order)

| Order | Check id | What it checks | Default on miss |
|---|---|---|---|
| 1 | `data_dir` | resolves and writes a probe file | FAIL if not writable |
| 2 | `config` | loads config.toml if present | FAIL on `ConfigError`; **OK** when missing (R7) |
| 3 | `claude_binary` | `shutil.which("claude")` (+ version on demand only) | FAIL if missing |
| 4 | `claude_auth` | the auth probe (§4.7) | SKIP if check 3 failed |
| 5 | `topics_summary` | lists topic dirs | OK if zero (`no topics yet — try remory init`) |
| 6 (per topic) | `schema_loadable` | `topic.load_topic(dir)` | FAIL on `TopicMetaError`/`SchemaError` |
| 7 (per topic) | `state_md_parseable` | `state.read_state` if present | FAIL on `StateParseError`; WARN if missing but `pending_count > 0` |
| 8 (per topic) | `state_md_canonical_form` | `--strict` only — `render_state(read_state(path)) == path.read_bytes()` | WARN on diff |
| 9 (per topic) | `state_md_schema_drift` | `_drift_sections` non-empty | WARN per drift section |
| 10 (per topic) | `lock_orphan` | `.lock` exists AND `is_locked()` False AND mtime > 1 hour | FAIL with `rm` hint |
| 11 (per topic) | `tmp_orphan` | any `*.tmp` files | WARN |
| 12 (per topic) | `backups_present` | populated state but no `.bak` | WARN |
| 13 (per topic) | `pending_orphan` | pending raw with `created < meta.last_consolidated` | WARN |
| 14 | `hook_installed` | `data_dir/.claude/settings.json` SessionEnd entry | INFO line; never fails (R6 wording) |
| 15 | `real_cli_probe` | `--probe-real-cli` only — round-trip path-encoding probe | FAIL on mismatch |

### 4.7 Auth probe (D4 + R5)

- Single `Backend.headless(prompt="ping", json_output=True, timeout_seconds=10)` call. **No retry.**
- Substring matching pattern (R5):
  ```python
  auth_keywords = ("login", "unauthorized", "authenticate")
  tail_lower = stderr_tail.lower()
  if any(k in tail_lower for k in auth_keywords):
      # FAIL
      ...
  ```
  Pin in `_check_claude_auth` docstring + dedicated test `test_check_claude_auth_case_insensitive_via_lower_once`.
- Classification:
  - `HeadlessResult` → **OK**, `logged in as <account_email_or_id>`.
  - `BackendNotFoundError` → SKIP (check 3 already failed).
  - `BackendTimeoutError` → WARN, `claude auth probe timed out after 10s. Check connectivity, then re-run.`
  - `BackendInvocationError(stderr_tail)` matching auth_keywords → **FAIL** with the §4.2 remediation. Otherwise WARN with truncated stderr tail.
  - `BackendOutputError` → WARN, `claude returned malformed output during auth probe.`
- No `~/.claude/...` filesystem inspection.

## 5. Sleep-output rendering (R4)

`ui.py::print_sleep_summary(result: SleepResult)` renders the orchestrator's structured result. The `SUCCESS_WITH_WARNINGS` path produces an italic line at the end of normal output:

**Locked literal (R4):**

```
note: critique step couldn't run; state.md is up to date but _review.md
wasn't refreshed.
```

This is a **sleep-output normal path**, not an error path. Pin the literal text in `print_sleep_summary` with a comment citing R4. Test name: `test_print_sleep_summary_success_with_warnings_critique_skip_renders_locked_note`.

Drift-drop notes from `SleepResult.notes` render similarly, prefixed with `note:` and the literal note text from the orchestrator (already pinned in Phase 3 to `dropped drift section '<title>' (not in schema; see logs)`).

## 6. Error mapping table (with D6 + D7 + R3)

Every command's top-level `try/except` routes through `cli/errors.py::format_error(exc, *, data_dir) -> tuple[str, int]`.

| Exception | User message | Remediation | Exit |
|---|---|---|---|
| `BackendNotFoundError` | `claude isn't on your PATH.` | `Install Claude Code, or check that the binary is named 'claude'. Then run remory doctor.` | 3 |
| `BackendAuthError` | `claude isn't logged in.` | `Run 'claude' once interactively to log in, then try again.` | 4 |
| `BackendTimeoutError` | `claude didn't respond within <N>s.` | `Try again. If it persists, check your connection and run remory doctor.` | 5 |
| `BackendInvocationError` | `claude exited with code <N>.` (+ dim block, stderr_tail truncated to 6 lines) | `Run remory doctor. Full logs at <data_dir>/logs/remory.log.` | 5 |
| `BackendOutputError` | `claude returned output I couldn't parse.` | `Rare; try again. If it persists, file a bug with the logs at <data_dir>/logs/remory.log.` | 5 |
| `LockBusyError` | `Another remory operation is in progress for topic '<name>'.` | `Wait for it to finish, then try again. If nothing else is running, run remory doctor — there may be a stale .lock.` | 6 |
| `SleepError(stage="extract")` | `Sleep couldn't read what was new in '<topic>'.` | `Re-run 'remory sleep <topic>'. If it persists, run remory doctor.` | 7 |
| `SleepError(stage="merge", backup_path=...)` | `Sleep stopped while merging '<topic>'. Your data is safe — backup at <backup_path>.` | `Re-run 'remory sleep <topic>'. If it persists, run remory doctor.` | 7 |
| `SleepError(stage="critique")` | (orchestrator already converts to non-fatal `SUCCESS_WITH_WARNINGS`; CLI never sees this exception) | n/a | 0 |
| `ExtractError` | `The model returned text that wasn't valid extraction output, twice. That's unusual.` | `Try 'remory sleep <topic>' once more. If the same thing happens, run remory doctor.` | 7 |
| `MergeError` | `Sleep failed to merge a section. This is a bug.` | `File an issue with the logs at <data_dir>/logs/remory.log.` | 7 |
| `CritiqueError` (R3) | `(orchestrator converts to SleepResult.warnings before reaching the CLI; this row exists as a contract reminder — if the CLI ever sees CritiqueError, that's a bug in the orchestrator)` | n/a | 0 |
| `TopicMetaError` | `Couldn't read meta.yaml for '<topic>': <message>.` | `Run remory doctor — it'll point at the line.` | 8 |
| `StateParseError` | `Couldn't read state.md for '<topic>': <message>.` | `Run remory doctor. The most recent backup is at <backups_dir>.` | 8 |
| `SchemaError` (init stub) | `Schema '<name>' isn't a thing I know.` | `Available: coaching, job-profile, workout.` (+ `Did you mean '<closest>'?` if `difflib` ≥0.6) | 2 |
| Topic missing (D6) | `Topic '<name>' doesn't exist yet.` | `Run remory init <name> to set it up. Existing topics: <comma-separated>` (or `Run remory init to set one up.` if zero topics). | 2 |
| Topic incomplete (D6) | `Topic '<name>' is in an incomplete state.` | `Run remory doctor to inspect — init could overwrite partial files.` | 2 |
| Topic exists already (D7, init stub) | (the pinned 3-line wording from D7) | (embedded in the message) | 1 |
| `RawWriteError` | `Couldn't write a new raw entry for '<topic>': <message>.` | `Disk full or permissions issue. Run remory doctor.` | 1 |
| `ConfigError` | `Your config.toml has a problem: <message>.` | `Edit <path> by hand, or remove it to fall back to defaults. Run remory doctor afterwards.` | 9 |
| `KeyboardInterrupt` | (no message; print a single newline so prompt isn't on `^C` line) | n/a | 130 |
| `Exception` (uncaught) | `Something unexpected went wrong: <repr>.` | `File a bug with the logs at <data_dir>/logs/remory.log.` | 99 |

## 7. Module split

### 7.1 New files (under `src/remory/`)

- **`cli.py`** — Typer app, command callbacks, root callback wiring `--config`/`--verbose`/`--debug`/`--version`.
- **`commands/__init__.py`** — empty.
- **`commands/init_cmd.py`** — `run_init(*, topic_name, schema_name, data_dir_override=None)`. Phase 4 stub per §3.9.
- **`commands/chat_cmd.py`** — `run_chat(*, topic_name, continue_session, backend_factory)`. Implements D1+D2+D6.
- **`commands/sleep_cmd.py`** — `run_sleep(*, topic_name, if_due, dry_run, backend_factory)`.
- **`commands/state_cmd.py`, `recent_cmd.py`, `review_cmd.py`, `ingest_cmd.py`, `topics_cmd.py`, `stats_cmd.py`** — one `run_<name>` per. Read-only commands acquire no lock.
- **`commands/doctor_cmd.py`** — `run_doctor(*, strict, probe_real_cli)`. Internal `_check_*` functions split for testability per §4.6.
- **`commands/version_cmd.py`** — `run_version() -> str`. Format: `remory <pep440-version>` from `importlib.metadata.version("remory")`.
- **`cli/__init__.py`** — empty.
- **`cli/errors.py`** — `format_error(exc, *, data_dir) -> tuple[str, int]`. Maps per §6.
- **`ui.py`** — `is_narrow()`, `is_tty()`, `use_color(cfg)`, `info`/`warn`/`error`/`success`, `print_doctor_report`, `print_sleep_summary` (with R4 literal pinned), `print_topics_table`, `prompt_text`, `prompt_choice`.
- **`wizard.py`** — Phase 5 stub: `WizardAnswers` dataclass, `run_wizard()` raises `NotImplementedError` with R2 message, `commit(answers, *, data_dir)` (Phase 5 fills body).
- **`logging_setup.py`** — `configure(*, level, log_file)`. Console at WARNING by default; `--verbose` → INFO; `--debug` → DEBUG. File handler at `<state_dir>/logs/remory.log` at DEBUG always (10MB rotation).

### 7.2 Modified files

- **`src/remory/state.py`** — add `is_canonical(path: Path) -> bool` per Phase 1b memory note.
- **`src/remory/__main__.py`** — wire `from remory.cli import app; app()`.
- **`pyproject.toml`** — verify `[project.scripts] remory = "remory.cli:app"` is present; add if not. No new dependencies.
- **`tests/fakes/fake_backend.py`** — add `with_auth_failure(stderr_tail=...)` constructor for auth-probe classification tests.
- **`CHANGELOG.md`** — extend `[Unreleased]` `### Added` per §10.

### 7.3 Untouched

`backends/{base,claude_code}.py`, `topic.py`, `raw.py`, `transcripts.py`, `paths.py`, `config.py`, `locking.py`, `sleep/orchestrator.py`, `schema.py`.

### 7.4 Cross-cutting decisions (architect defaults, accepted)

- **CC1** chat-vs-SessionEnd → policy (b), ADR 0001.
- **CC2** chat lock → fork+wait, ADR 0001.
- **CC3** `--if-due` → iterates topics; includes iff `pending_count >= trigger_threshold`; per-topic sleeps independent; lock-busy bucketed into per-topic FAIL line.
- **CC4** `--version` → `remory <pep440-version>` only.
- **CC5** exit codes → 0 success, 1 generic runtime, 2 usage, 3 backend-not-found, 4 backend-auth, 5 backend-other, 6 lock-busy, 7 sleep-pipeline, 8 data-parse, 9 config, 99 uncaught, 130 SIGINT.
- **CC6** `--config <path>` → replaces `REMORY_CONFIG_FILE` for this invocation; on root callback.
- **CC7** `--verbose`/`--debug` → INFO/DEBUG to stderr; both also enable file handler. Default WARNING.
- **CC8** `KeyboardInterrupt` → print newline, exit 130. Wizard COMMIT block ignores SIGINT until in-flight atomic write completes.
- **CC9** read-only commands take no lock; rely on atomic-write contract.
- **CC10** auth probe runs whenever claude binary is present; no opt-out flag in v0.1.

## 8. ADR 0001 — chat ↔ SessionEnd raw-write coordination

File: `docs/adr/0001-chat-vs-session-end-hook-raw-write-coordination.md`

**Status:** Accepted. Decided in Phase 4. Implemented in Phase 4 (chat parent always writes); Phase 6 ships the deferring hook.

**Context:** Two surfaces produce raw entries from a Claude Code chat session: `remory chat` (in-process, after subprocess exit) and the SessionEnd hook (out-of-process, fired by claude including direct invocations). Phase 2 shipped shared helpers (`transcripts.to_markdown`, `raw.write_raw`) without committing to which surface owns the write. Under fork+wait (CC2), the chat parent holds `topic_lock` continuously across the subprocess; the hook is invoked by claude as a separate process with no shared environment.

**Decision:** Chat-as-parent is canonical writer. The Phase 6 hook script defers via `locking.is_locked(topic_dir)` non-blocking probe at hook entry; if held, the hook skips and exits 0 silently (debug log only). When the hook acquires the lock, it scans `list_raw(topic_dir, status=None)` for an existing raw entry with the same `frontmatter.session_id` as a belt-and-suspenders idempotency floor before writing. `chat_cmd.py` does not branch on hook presence — it always writes.

**Consequences:** Chat-parent crash window (post-exit, pre-write) is *covered* by the hook acting as safety net (when Phase 6 ships). Hook crash window has no recovery path; accepted risk. Phase 4 ships a working chat with no hook; direct `claude` invocations outside `remory chat` produce no raw entry until Phase 6 — Phase 4 doctor surfaces this with the R6 INFO line. The `is_locked()` probe + session-id scan combination removes any need for env-var plumbing or sentinel files.

**Alternatives considered:**
- *(a) Hook canonical, chat detects via session-id scan and skips.* Rejected: inverts ownership.
- *(c) Symmetric idempotent writes via session-id-keyed sentinel.* Rejected: symmetry is illusory because surfaces have asymmetric context.

## 9. ADR 0002 — wizard COMMIT partial-failure policy

File: `docs/adr/0002-wizard-commit-partial-failure-leave-as-is.md`

**Status:** Accepted. Decided in Phase 4. Implemented in Phase 5 wizard.

**Context:** The wizard COMMIT phase writes multiple topic directories sequentially. If COMMIT fails partway (e.g. disk fills after topic A completes but before topic B), the wizard must choose between rolling back topic A (atomic-across-topics) or leaving it as-is (per-topic atomic).

**Decision:** Leave-as-is. Each topic is independently atomic. Doctor is the recovery surface. User-facing message: `Stopped mid-write at topic '<name>'. Topic '<prior>' was created successfully. Run remory doctor to inspect, or remory init <name> to retry the failed topic.`, exit 1.

**Consequences:** Each topic A is independently valid, just lonely. User can `remory init <name>` to add the failed topic later, or `remory doctor` to inspect. Rollback would require teardown logic with its own correctness surface and would violate the per-topic atomic-write contract.

**Alternatives considered:**
- *Atomic across all topics with rollback.* Rejected: teardown complexity; teardown-of-teardown failure mode.
- *Ship `remory init --retry-failed`.* Rejected as redundant: same data outcome; user reaches for `remory doctor` + `remory init <single-topic>` under recommended.

## 10. CHANGELOG draft

Insert under `[Unreleased]` → `### Added`. Implementer extends/trims as commands actually land.

```markdown
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
```

## 11. Test surface (binding)

### 11.1 Unit tests

- `tests/unit/test_cli_errors.py` — row-by-row tests for §6 table; D6 missing/incomplete tests; D7 existing-topic test; R3 CritiqueError contract-reminder test.
- `tests/unit/test_doctor_checks.py` — per-check OK/WARN/FAIL/SKIP; R5 case-insensitive auth-keyword test (`test_check_claude_auth_case_insensitive_via_lower_once`); R7 missing-config-is-ok test; R6 hook-installed phrasing test.
- `tests/unit/test_state_is_canonical.py` — canonical helper: true on render output, false on hand-edited unsorted keys, false on single-quoted ISOs, does not modify file.
- `tests/unit/test_init_cmd.py` — schema flag required (R2 wording); unknown schema with/without close match; topic name validation; D7 existing-topic refusal; meta.yaml + state.md skeleton creation; lock acquired during writes.
- `tests/unit/test_chat_cmd.py` — D1 fork+wait lock invariant; D2 idempotency by session_id; D6 three-case precondition (missing/incomplete/complete); resume flag; threshold suggestion.
- `tests/unit/test_sleep_cmd.py` — single-topic delegates; `--dry-run` passthrough; `--if-due` iterates and gates; zero eligible exits 0; one fail continues; missing topic + no `--if-due` exits 2; lock-busy exits 6.
- `tests/unit/test_simple_commands.py` — state/recent/review/ingest/topics/stats/--version contracts.
- `tests/unit/test_ui.py` — narrow detection, color suppression, doctor report rendering, glyph fallback; **R4 test** `test_print_sleep_summary_success_with_warnings_critique_skip_renders_locked_note` asserting the literal critique-skip note.
- `tests/unit/test_logging_setup.py` — level mapping, file handler.

### 11.2 Integration tests

- `tests/integration/test_e2e_chat_then_sleep.py` — §14-mandated end-to-end. Init via stub → chat against fake_claude → sleep → assertions.
- `tests/integration/test_doctor_e2e.py` — clean exit 0; orphan lock exit 1; `--strict` non-canonical warn; auth probe stderr-tail "login" → unauthenticated; R7 missing-config OK in clean run.
- `tests/integration/test_init_then_chat.py` — stub creates topic; chat writes first raw; existing-topic refusal does not clobber existing state.md.

### 11.3 Forward-debt test names (Phase 6, reserved here)

- `test_session_end_hook_writes_raw_entry_when_topic_lock_is_free_and_no_duplicate_session_id_exists`
- `test_concurrent_chat_parent_and_session_end_hook_produce_exactly_one_raw_entry_keyed_by_session_id`

### 11.4 Fixtures

`tests/fakes/fake_doctor_checks.py` (CheckResult builder); `tests/fixtures/state_md_noncanonical.md` (handcrafted fixture); `tests/fakes/fake_backend.py::FakeBackend.with_auth_failure(stderr_tail=...)` constructor.

## 12. Forward-debts (v0.2 candidates)

1. `remory delete-topic <name>` — wraps existing-topic destruction (D7).
2. `--exit-on-warn` semantics (D10).
3. Time-based `--if-due` threshold.
4. Local Claude config inspection for auth probe (only if a stable, documented path emerges).
5. Wizard `--non-interactive` flag.

## 13. Process expectations

- **Surface ambiguity, do not improvise.** If something in the existing code (state.py, raw.py, schema.py, atomic.py, locking.py, backends/, sleep/orchestrator.py) doesn't match what this plan implies, stop and report what you found and what you'd suggest.
- **Tests with the change in the same pass; no "tests later."**
- **Type-hinted code; pyright strict for `src/`.** No `# type: ignore` without inline justification.
- **No `# noqa` and no `pytest.skip`** without explanation.
- **CHANGELOG: implementer extends §10 block as commands actually land.**
- **No commits from implementer.** When done, surface the work and stop. The user reviews; CI green across all 6 jobs is the closing gate.

## 14. Verification

After your changes:

```
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest
```

All green locally. Then return a short summary: which files changed, which test names you added, anything you discovered that the plan didn't anticipate. Do not commit.
