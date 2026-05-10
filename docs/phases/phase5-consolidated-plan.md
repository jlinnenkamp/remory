# Phase 5 — Wizard implementation (binding consolidated plan)

This document is the binding contract for Phase 5 implementation. It consolidates the architect plan with all user-applied refinements. **The user-facing contract from `docs/phases/phase4-consolidated-plan.md` §2 is locked. Phase 5 implements; it does not redesign. Strings are not subject to revision in Phase 5.** Any deviation is an ambiguity to surface, not an improvement to make.

Spec anchors: `INSTRUCTIONS.md` §11, §14. Memory notes: `feedback_silently_means_logged`, `feedback_log_omit_prompt_adjacent_fields`, `feedback_test_names_encode_contract`, `feedback_no_silent_data_loss`, `feedback_serialization_format_choice`, `feedback_commit_message_one_draft`, `feedback_reviewer_nit_default`, `feedback_ux_phase_concrete_strings`. ADRs: 0002 (chat coordination, Phase 5 doesn't touch), 0003 (wizard COMMIT no-rollback).

## 1. User-confirmed decisions

**D1. LLM-letter fallback catches `BackendError` base class** (architect's #1, recommended). `BackendTimeoutError`, `BackendInvocationError`, `BackendOutputError`, `BackendAuthError`, `BackendNotFoundError`, plus future subclasses, all degrade to the hand-written fallback. Empty/whitespace `HeadlessResult.text` also triggers fallback. Reasoning: wizard's job is to get the user set up; doctor is the diagnostic surface for auth/binary issues. Forward-compatible by default.

**D2. Phase 5 letter step uses `Backend.headless(agent=None)` directly** (architect's #6, recommended). The `wizard.md` Claude Code subagent is **Phase 6 scope**, shipped alongside the rest of the production-time `.claude/` ecosystem. Phase 5's letter prompt is composed by a private `_compose_letter_prompt(answers)` helper.

**D3. `remory init` (no args) routes to wizard; `topic_name` becomes optional** (architect's #11, recommended). Three CLI shapes:
- `remory init` → wizard.
- `remory init <name> --schema <schema>` → existing stub (Phase 4 D7 wording for existing-topic refusal).
- `remory init <name>` (no schema) → R2 wording error, exit 2.

**D4. Two Q1 specifics (user-pinned):**

- **WARNING log discipline.** When fallback fires, emit one WARNING log with structured fields:
  ```python
  _log.warning(
      "wizard letter step degraded to fallback paragraph",
      extra={
          "exception_type": type(exc).__name__ if exc else "empty_model_output",
          "wizard_step": "letter",
      },
  )
  ```
  **Default-omit `stderr_tail`** per memory note `feedback_log_omit_prompt_adjacent_fields`; do not log response bodies or prompt fragments. The architect's earlier `extra` shape that included `stderr_tail` is overridden here.

- **Fallback template byte-pinned.** Architect's `_FALLBACK_TEMPLATE` is the contract:
  ```python
  _FALLBACK_TEMPLATE = (
      "(I couldn't reach the model just now, so this is a quick stand-in.) "
      "{name_clause}You picked {topics_clause}. {wish_clause}"
      "I'll keep what you bring me here, and only what you bring me."
  )
  ```
  With clauses:
  - `name_clause`: `f"Hi {answers.name}. "` if `answers.name` is set, else `""`.
  - `topics_clause`: human-readable join — 1 topic → bare name; 2 → `"X and Y"`; 3+ → Oxford comma `"X, Y, and Z"`.
  - `wish_clause`: `f'You said: "{answers.wish}". '` if `answers.wish` is set, else `""`.
  
  Test `test_compose_fallback_letter_pins_paragraph_for_canned_answers` byte-equal-asserts against canned `WizardAnswers(name="Sam", chosen_topics=["workout"], knobs_by_topic={"workout": {"tone": "warm", "strictness": "balanced"}}, wish="stop forgetting what I told it")`. Expected paragraph (single line, single paragraph):
  ```
  (I couldn't reach the model just now, so this is a quick stand-in.) Hi Sam. You picked workout. You said: "stop forgetting what I told it". I'll keep what you bring me here, and only what you bring me.
  ```
  No drift: the test catches divergent implementations of "composed from the answers."

## 2. Silent-default ambiguities (architect's recommendations stand)

**#2 Existing-topic refusal at COMMIT, not at validator.** Step 2 shows all three topics; refusal fires inside the COMMIT block with ADR 0003 wording. Filtering the menu would drift the binding §3.3 wording.

**#3 about-me.md still written when user skips both name AND wish.** Letter paragraph + facts block (with empty fields after the colons) is still useful.

**#4 3-line CLAUDE.md placeholder bytes reused from Phase 4.** Refactor to a shared module-level constant in `paths.py` or a small new `templates.py` module; init_cmd and the wizard import the same string. Phase 6 owns the real template.

**#5 Three-strikes counter is per-question, not wizard-global.** Per plan §3.4.4 "consecutive" inside the option-style block. `_AttemptCounter` resets at the start of each `_prompt_with_validator` call.

**#7 COMMIT per-topic order is selection-order, not lexicographic.** Matches §3.7 outro; trace-of-failure clarity.

**#8 New `WizardSigintDuringCommitError` exception.** Raised in `_commit.py` after re-catching the deferred-SIGINT KeyboardInterrupt. `cli/errors.py::format_error` adds a row with the plan §3.8 locked message and exit 130.

**#9 Windows SIGINT support: ADR 0004 captures the gap.** `signal.pthread_sigmask` is POSIX-only. Windows uses a flag-based handler with a known race window (best-effort). Captured in `docs/adr/0004-wizard-sigint-windows-best-effort.md`. Phase 5 lands on POSIX; Windows ships best-effort.

**#10 `paths.about_me_file(data_dir)` helper** is a one-line addition to `paths.py`.

## 3. Module split

**`wizard/` package** replacing the Phase 4 `wizard.py` single file. `wizard/__init__.py` re-exports the public surface (`WizardAnswers`, `WizardNotBuiltError`, `WIZARD_NOT_BUILT_MESSAGE`, `run_wizard`, `commit`) so existing callers (`init_cmd.py`, `cli/errors.py`) continue working.

```
src/remory/wizard/
    __init__.py           re-exports public surface
    _orchestrator.py      _WizardOrchestrator class; .run() returns (answers, letter)
    _steps.py             pure step functions (welcome, name, pick_topics, …, outro)
    _letter.py            compose_letter, compose_fallback_letter, _compose_letter_prompt
    _commit.py            commit() + _deferred_sigint context manager + _about_me_bytes
    _validators.py        validate_name, validate_topic_picks, validate_choice_with_skip, validate_wish
```

`_orchestrator.py` is internal (underscore prefix). Distinct from `sleep/orchestrator.py`.

## 4. State machine implementation

**Small class `_WizardOrchestrator` in `_orchestrator.py`.** Mutable `WizardAnswers` accumulator (Phase 4 dataclass shape preserved; orchestrator assigns step results). Step functions in `_steps.py` are pure I/O on stdin/stdout; they do not import `backends`.

**LLM call hoisted to orchestrator**, not in step functions. `_compose_letter` swallows backend exceptions and returns the fallback string; never propagates BackendError. `KeyboardInterrupt` passes through.

```python
class _WizardOrchestrator:
    def __init__(self, *, console: Console, backend: Backend, data_dir: Path) -> None: ...
    def run(self) -> tuple[WizardAnswers, str]:
        """Drive welcome → name → pick → per-topic → wish → letter → outro.
        Returns (answers, letter_text). Does not commit."""
```

## 5. SIGINT handling (architect §4)

**Mechanism: `signal.pthread_sigmask` (POSIX) wrapped in a `contextlib.contextmanager`.** Per-write granularity — each `atomic_write_*` / `write_meta` / `write_state` call wraps in `_deferred_sigint()`. SIGINT delivered while masked is queued by the kernel; delivered the moment we unmask.

```python
@contextlib.contextmanager
def _deferred_sigint() -> Iterator[None]:
    """Mask SIGINT for the duration of the block. On exit, unmask;
    any queued SIGINT is delivered immediately and propagates as
    KeyboardInterrupt to the caller. POSIX-only; Windows uses a
    flag-based handler with a known race window (ADR 0004)."""
```

**Pre-COMMIT SIGINT:** standard `KeyboardInterrupt` handling. The wizard catches at the outer boundary, prints `Stopped. No files written. Run remory init when you're ready.` to stderr, re-raises so `cli.py`'s top-level handler maps to exit 130.

**During-COMMIT SIGINT:** `_deferred_sigint` defers until the in-flight atomic write completes; the queued signal is delivered at the unmask. `_commit.py` catches the resulting `KeyboardInterrupt`, raises `WizardSigintDuringCommitError`. `cli/errors.py` maps to: `Stopped mid-write. Some files may exist. Run remory doctor to inspect.`, exit 130.

**Double SIGINT during the deferred window:** queued by kernel; delivered once at unmask. No special handling. The window is short.

**Windows:** flag-based handler; best-effort. ADR 0004.

## 6. LLM-letter failure handling (architect §5)

Catches `BackendError` base + empty/whitespace text per D1. WARNING log per D4. Fallback paragraph per `_FALLBACK_TEMPLATE` per D4.

`backend.headless()` parameters:
```python
backend.headless(
    prompt=_compose_letter_prompt(answers),
    agent=None,            # D2: subagent is Phase 6
    cwd=None,
    json_output=False,
    timeout_seconds=30,    # short — user is at the terminal
)
```

**`_compose_letter_prompt(answers)` shape is pinned (R3 refinement):**

System prompt (constant, pinned literal):
```
You are the Remory wizard. The user just finished a short setup interview.
Read what they shared back to them as one warm paragraph in second person,
3 to 5 sentences, no preamble, no headings, no bullet points. Do not
restate the topic descriptions; reflect what *this* user said. End on a
note that signals you'll keep what they bring you.
```

User prompt structure (fields concatenate in this order, omit any unset
field's section entirely):
```
Name: {answers.name}
Topics chosen: {answers.chosen_topics, comma-separated, selection order}
Knobs per topic:
  {topic}: tone={knobs.tone}, strictness={knobs.strictness}
  ...
What they're hoping for: {answers.wish}
```

Output format request (appended to user prompt as a final line, pinned):
```
Respond with one paragraph. No preamble, no headings, second person, 3 to 5 sentences.
```

Internal — not user-visible — not contract-locked across versions, but
locked within Phase 5 so Phase 6's subagent migration has a baseline to
compare against. Tested by:
- `test_compose_letter_prompt_includes_name_topics_wish_for_set_answers`
- `test_compose_letter_prompt_omits_unset_name_section`
- `test_compose_letter_prompt_omits_unset_wish_section`
- `test_compose_letter_prompt_renders_knobs_per_topic_in_selection_order`
- `test_compose_letter_prompt_ends_with_pinned_output_format_request`

**about-me.md byte format (pinned per architect §5.3):**
```
{letter_paragraph}

---
name: {name_or_blank}
topics: {topics_csv}
wish: {wish_or_blank}
```
Trailing newline at EOF. Selection-order topics (`"job-profile, workout"`). `atomic_write_text` via `paths.about_me_file(data_dir)`.

## 7. `prompt_*` helper audit (architect §6)

**Do NOT modify `ui.py`'s public API.** Add **one** new helper:

```python
def prompt_line(
    prompt: str,
    *,
    console: Console | None = None,
    input_fn: object | None = None,
) -> str:
    """Read one raw line from stdin (no .strip()). Returns the line
    including no trailing newline. Wizard validators check newline
    presence — they can't be applied after .strip()."""
```

Wizard layer wraps `prompt_line` with re-prompt loop + validator + per-question 3-strikes counter:

```python
class WizardThreeStrikesError(Exception):
    """User exhausted three consecutive invalid attempts on a single prompt."""

def _prompt_with_validator(
    prompt: str,
    validator: Callable[[str], _ValidatedOrReason],
    *,
    console: Console,
    input_fn: object | None,
) -> str | _Skipped:
    """Prompt + re-prompt loop. On three consecutive invalid attempts
    on the SAME question, raise WizardThreeStrikesError."""
```

`cli/errors.py::format_error` adds:
```python
if isinstance(exc, WizardThreeStrikesError):
    return ("Three tries — let's stop here. Run remory init again when you're ready.\n", 2)
```

**Skip handling:**
- Step 3.x option questions: `s`/`skip`/`Skip`/`S` (case-insensitive) returns the schema's default. Skip-alias mapping in `_validators.py`; resolved default read from `schema.defaults.{tone,strictness}`.
- Step 1 / Step 4 free-text: only literal `[skip]` (case-sensitive, brackets) counts as skip.

## 8. COMMIT block mechanics (architect §7)

Order inside the COMMIT block:
```
1.  data_dir.mkdir(parents=True, exist_ok=True)
2.  paths.topics_dir().mkdir(parents=True, exist_ok=True)
3.  for topic_name in answers.chosen_topics:        # selection order (#7)
        topic_dir = paths.topic_dir(topic_name)
        if topic_dir.exists():
            raise TopicExistsError(...)
        topic_dir.mkdir(parents=False, exist_ok=False)
        with topic_lock(topic_dir, timeout=0.0):
            with _deferred_sigint():
                write_meta(topic_dir, _build_meta(answers, topic_name, schema))
            with _deferred_sigint():
                write_state(paths.state_file(topic_dir), _build_state_doc(schema))
            with _deferred_sigint():
                atomic_write_text(paths.claude_md_file(topic_dir), _CLAUDE_MD_PLACEHOLDER.format(schema_name=topic_name))
4.  with _deferred_sigint():
        atomic_write_text(paths.about_me_file(data_dir), _about_me_bytes(answers, letter))
```

**3-line CLAUDE.md placeholder (pinned bytes, shared with init_cmd):**
```python
_CLAUDE_MD_PLACEHOLDER = (
    "# Topic: {schema_name}\n"
    "Do not edit state.md. It is updated only during sleep.\n"
    "See state.md for the canonical context for this topic.\n"
)
```
Refactor to shared module so both `init_cmd.py` and `wizard/_commit.py` import the same constant.

**Partial-failure user-facing message (ADR 0003, two branches):**

When ≥1 topic completed before failure:
```
Stopped mid-write at topic '<name>'. Topic '<prior>' was created
successfully. Run remory doctor to inspect, or remory init <name> to
retry the failed topic.
```

When no prior topic completed:
```
Stopped mid-write at topic '<name>'. Run remory doctor to inspect, or
remory init <name> to retry the failed topic.
```

Implementer adds `WizardCommitPartialError(failed_topic, prior_topic_or_none)`; `cli/errors.py::format_error` formats both branches. Exit 1.

About-me.md failure after all topics complete: `WizardAboutMeError`, message `All topics created, but about-me.md couldn't be written. Run remory doctor.`, exit 1.

## 9. `init_cmd` integration (architect §8)

Update `cli.py`'s `init` callback per D3:
- `topic_name: str | None = None` (was required).
- Both `topic_name` and `schema_name` None → `run_wizard(...)`.
- Exactly one set → R2 wording error, exit 2.

**Dispatch order (R3 refinement, binding):** when `topic_name` is provided,
check `topic-already-exists` **FIRST**, then check `--schema` presence.
This means a user typing `remory init workout` (where `workout` already
exists) gets the D7 "already exists" guidance with the recovery path,
NOT the R2 "pass --schema or run with no args" wording. A typo'd
existing-topic name should not be redirected to the wizard.

```
if topic_name is None and schema_name is None:
    run_wizard(...)
    return
if topic_name is None:  # schema set, no name
    raise UsageError("...")
if topic_dir(topic_name).exists():
    raise TopicExistsError(...)   # D7 wording, exit 1   <-- FIRST
if schema_name is None:
    raise WizardRedirectError(...)  # new R2 wording, exit 2  <-- THEN
run_init(topic_name=..., schema_name=...)
```

**R2 wording update (R3 refinement, binding):** the Phase 4 R2 wording
("The interactive wizard isn't built yet. For now, pass --schema to pick
a built-in: --schema job-profile, --schema workout, or --schema coaching.")
becomes false the moment Phase 5 ships. Phase 5 replaces it with:

```
Pass --schema to pick a built-in directly (--schema job-profile,
--schema workout, --schema coaching), or run `remory init` with no
arguments for the interactive wizard.
```

The new wording lives in `wizard/__init__.py` as the
`WIZARD_REDIRECT_MESSAGE: Final[str]` canonical constant.
`WIZARD_NOT_BUILT_MESSAGE = WIZARD_REDIRECT_MESSAGE` is preserved as a
deprecated alias for one release; remove in v0.2. The exception class
follows the same pattern: `WizardRedirectError` is canonical;
`WizardNotBuiltError = WizardRedirectError` is the alias. Both class
and message aliases are exported from `wizard/__init__.py`. `cli/errors.py`
maps either class (they are the same class now) to exit 2 with this
wording.

`commands/init_cmd.py::run_init` keeps its non-interactive stub
semantics for the `--schema` path. The wizard path is in the new
`wizard/` package.

## 10. CHANGELOG update

Replace the existing `remory init` bullet under `[Unreleased] / ### Added`:

```markdown
- `remory init <topic> --schema <name>` — create a topic directory from a
  built-in schema (`job-profile`, `workout`, `coaching`). Refuses to overwrite
  an existing topic.
- `remory init` (no arguments) — interactive first-run wizard. Walks through
  picking one or more built-in topics, sets per-topic tone and strictness
  knobs, and writes a short `about-me.md` at the data directory root. The
  wizard reads the user's answers back to them as a paragraph generated by
  Claude; if the model is unreachable (offline, not logged in, slow), the
  wizard falls back to a hand-composed paragraph and continues without
  failing. Pressing Ctrl+C before the wizard begins writing leaves no files
  behind; pressing Ctrl+C during the write phase finishes the in-flight file
  and stops, surfacing partial state via `remory doctor` per the
  per-topic-atomic contract.
```

The other bullets (`remory chat`, `remory sleep`, etc.) are unchanged.

## 11. Test surface (binding)

### 11.1 Unit — validators (`tests/unit/test_wizard_validators.py`)
- `test_validate_name_returns_value_for_valid_input`
- `test_validate_name_rejects_empty_string_with_blank_reason`
- `test_validate_name_rejects_over_60_chars_with_too_long_reason`
- `test_validate_name_rejects_input_containing_newline_with_newline_reason`
- `test_validate_name_accepts_literal_bracketed_skip_token_and_returns_skipped`
- `test_validate_name_rejects_bare_word_skip_as_value`
- `test_validate_topic_picks_empty_returns_all_three_in_lex_order`
- `test_validate_topic_picks_single_returns_one_topic`
- `test_validate_topic_picks_comma_separated_returns_selection_order`
- `test_validate_topic_picks_space_separated_returns_selection_order`
- `test_validate_topic_picks_rejects_zero_with_out_of_range_reason`
- `test_validate_topic_picks_rejects_four_with_out_of_range_reason`
- `test_validate_topic_picks_rejects_alphabetic_with_parse_reason`
- `test_validate_topic_picks_rejects_multiline_paste_with_parse_reason`
- `test_validate_choice_with_skip_accepts_1_and_2_as_options`
- `test_validate_choice_with_skip_accepts_s_skip_S_Skip_case_insensitive`
- `test_validate_choice_with_skip_rejects_zero_three_alpha_zerodigit_multiline`
- `test_validate_wish_returns_value_for_valid_input`
- `test_validate_wish_rejects_empty_with_blank_reason`
- `test_validate_wish_rejects_over_500_with_too_long_reason`
- `test_validate_wish_rejects_newline_with_single_sentence_reason`
- `test_validate_wish_accepts_literal_bracketed_skip_token`

### 11.2 Unit — three-strikes (`tests/unit/test_wizard_three_strikes.py`)
- `test_prompt_with_validator_reraises_after_three_consecutive_invalid_attempts_on_same_prompt`
- `test_prompt_with_validator_resets_attempt_counter_after_valid_input_within_run`
- `test_format_error_maps_wizard_three_strikes_to_locked_message_exit_2`

### 11.3 Unit — letter (`tests/unit/test_wizard_letter.py`)
- `test_compose_letter_returns_model_text_stripped_on_backend_success`
- `test_compose_letter_falls_back_when_backend_raises_timeout`
- `test_compose_letter_falls_back_when_backend_raises_invocation_error`
- `test_compose_letter_falls_back_when_backend_raises_output_error`
- `test_compose_letter_falls_back_when_backend_raises_auth_error`
- `test_compose_letter_falls_back_when_backend_raises_not_found_error`
- `test_compose_letter_falls_back_when_model_returns_empty_text`
- `test_compose_letter_falls_back_when_model_returns_whitespace_only_text`
- `test_compose_fallback_letter_pins_paragraph_for_canned_answers` (byte-equal pin per D4)
- `test_compose_fallback_letter_omits_name_clause_when_name_unset`
- `test_compose_fallback_letter_omits_wish_clause_when_wish_unset`
- `test_compose_fallback_letter_uses_oxford_comma_for_three_topics`
- `test_compose_letter_logs_warning_with_exception_type_and_wizard_step_extras_only` (D4 omit-stderr-tail)

### 11.4 Unit — about-me bytes (`tests/unit/test_wizard_about_me.py`)
- `test_about_me_bytes_pins_format_with_letter_name_topics_wish_set`
- `test_about_me_bytes_renders_blank_after_colon_for_omitted_name`
- `test_about_me_bytes_renders_blank_after_colon_for_omitted_wish`
- `test_about_me_bytes_orders_topics_in_selection_order_not_lex`

### 11.5 Unit — SIGINT mechanism (`tests/unit/test_wizard_sigint.py`)
- `test_deferred_sigint_masks_signal_during_block_then_reraises_on_exit`
- `test_deferred_sigint_unmask_propagates_queued_signal_as_keyboard_interrupt_to_caller`
- `test_deferred_sigint_double_sigint_within_block_delivered_once_at_unmask`

### 11.6 Unit — COMMIT (`tests/unit/test_wizard_commit.py`)
- `test_commit_creates_data_dir_and_topics_dir_when_absent`
- `test_commit_writes_meta_state_claude_md_and_about_me_for_one_topic_happy_path`
- `test_commit_writes_artefacts_in_selection_order_for_two_topics`
- `test_commit_writes_artefacts_in_selection_order_for_three_topics`
- `test_commit_releases_topic_locks_after_per_topic_writes_complete`
- `test_commit_raises_partial_when_second_topic_write_state_fails_leaving_first_topic_intact`
- `test_commit_raises_partial_with_no_prior_clause_when_first_topic_fails`
- `test_commit_refuses_at_commit_when_topic_dir_already_exists_with_topic_exists_error`
- `test_commit_writes_about_me_after_all_topics_complete`
- `test_commit_raises_about_me_error_when_about_me_write_fails_after_topics_complete`

### 11.7 Unit — orchestrator wiring (`tests/unit/test_wizard_orchestrator.py`)
- `test_run_wizard_threads_answers_through_all_steps_in_linear_order`
- `test_run_wizard_skips_per_topic_block_for_unselected_topics`
- `test_run_wizard_uses_schema_defaults_when_user_skips_q1_q2`
- `test_run_wizard_pre_commit_keyboard_interrupt_propagates_without_writing_files`

### 11.8 Integration (`tests/integration/test_wizard_e2e.py`)
- `test_remory_init_runs_wizard_when_invoked_without_topic_or_schema_flag` — happy path. CliRunner with stdin scripted: `Sam\n1,2\n1\n1\n1\n1\nstop forgetting\n`. Assert: exit 0, two topic dirs, about-me.md exists with correct bytes, lock files released.
- `test_remory_init_wizard_renders_fallback_letter_when_fake_backend_raises_timeout` — assert about-me.md first paragraph matches the fallback template; assert WARNING logged with exception_type/wizard_step extras only.
- `test_remory_init_wizard_keyboard_interrupt_pre_commit_writes_no_files_and_exits_130`
- `test_remory_init_wizard_keyboard_interrupt_during_first_topic_write_completes_in_flight_then_exits_130`
- `test_remory_init_wizard_partial_failure_at_second_topic_leaves_first_topic_intact_and_exits_1`
- `test_remory_init_wizard_refuses_when_chosen_topic_already_exists_with_topic_exists_message`

### 11.9 Fixtures
- `tests/fakes/fake_backend.py::FakeBackend.with_letter_text(text: str)` — canned text from `headless()`.
- `tests/fakes/fake_backend.py::FakeBackend.with_letter_failure(exc_class: type[BackendError], **kwargs)` — raises on `headless()`.
- `tests/fakes/scripted_input.py` — list-of-lines wrapper, raises `EOFError` when exhausted, optionally raises `KeyboardInterrupt` at a designated index.

## 12. Files (binding)

**Create:**
- `src/remory/wizard/__init__.py` (re-exports), `_orchestrator.py`, `_steps.py`, `_letter.py`, `_commit.py`, `_validators.py`
- `docs/adr/0004-wizard-sigint-windows-best-effort.md`
- 8 new test files under `tests/unit/test_wizard_*.py` and `tests/integration/test_wizard_e2e.py`
- `tests/fakes/scripted_input.py`

**Modify / replace:**
- `src/remory/wizard.py` → **delete** (replaced by `wizard/` package). Public-symbol surface preserved via `wizard/__init__.py`.
- `src/remory/paths.py` — add `about_me_file(data_dir)`.
- `src/remory/ui.py` — add `prompt_line` (raw, no strip).
- `src/remory/cli/errors.py` — add `WizardThreeStrikesError`, `WizardCommitPartialError`, `WizardAboutMeError`, `WizardSigintDuringCommitError` rows.
- `src/remory/cli/__init__.py` — make `init` `topic_name` optional; route empty-args to `run_wizard`.
- `src/remory/commands/init_cmd.py` — extract shared `_CLAUDE_MD_PLACEHOLDER` constant.
- `tests/fakes/fake_backend.py` — `with_letter_text`, `with_letter_failure` constructors.
- `CHANGELOG.md` — replace `remory init` bullet per §10.

**Untouched:** `backends/`, `topic.py`, `raw.py`, `transcripts.py`, `config.py`, `locking.py`, `atomic.py`, `state.py`, `schema.py`, `sleep/`, `commands/{chat,sleep,doctor,…}_cmd.py`.

## 13. Process expectations

- **Surface ambiguity, do not improvise.** If existing code shapes don't match the plan implies, stop and report with a recommended default.
- **Tests with the change in the same pass.** No "tests later."
- **Type-hinted code; pyright strict for `src/`.** No `# type: ignore` without inline justification.
- **No new dependencies.** typer + rich are already deps; tenacity unused in this phase.
- **No commits from implementer.** When done, surface the work and stop. CI green across all 6 jobs is the closing gate.
- **CHANGELOG: implementer extends §10 block as the wizard actually lands.** No phase numbers; describe user-observable behavior.

## 14. Verification

```
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest
```

All green locally. Then return a summary: files created/modified, test count added, deviations from the plan with reasons, forward-debts surfaced.
