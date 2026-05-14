# ADR 0006: Wizard rearchitected as a Claude Code subagent

**Status:** Accepted. Decided in Phase 6.

## Context

Phase 5 shipped a Python-driven wizard: linear prompt loop in
`_steps.py`, hand-written `validate_*` functions in `_validators.py`,
three-strikes counter, and a single LLM call hoisted into the
orchestrator to compose the "letter" paragraph. The UX worked, but two
things rubbed against the spec's §11 promise that the wizard "earns its
warm and a little addictive feel":

1. **Two voices.** The Python prompts ("Pick one or more by number,
   separated by commas") and the model paragraph ("Hi Sam. I'll keep
   what you bring me here") read like two different products joined at
   the seam. The spec asks for one voice, and that voice has to be the
   model's.
2. **The skip path was a form, not a conversation.** Every option
   question carried an `[s] Skip — use the default ("warm")` line. That
   is what an opinionated form looks like; it is not what a warm
   first-run interview looks like.

A literal reading of `INSTRUCTIONS.md` §11 ("the wizard runs as a single
Claude Code session driven by the `wizard` subagent, with the harness
orchestrating turns") says the model owns the conversation, not the
harness. Phase 5 implemented a stand-in because Claude Code's subagent
machinery had not yet been wired into the backend; Phase 6 finishes
that work.

## Decision

The Phase 6 wizard is a single `claude --agent wizard` session. The
harness owns three things:

1. **Preflight.** Reuse the doctor's `_check_claude_binary` and
   `_check_claude_auth` probes. If either is not OK, refuse to launch
   and point the user at `remory doctor`. There is no offline
   fallback. The wizard's voice is the model's voice; if the model is
   unreachable, the wizard does not run at all.
2. **JSON validation.** The `wizard.md` subagent writes
   `<run_dir>/answers.json` to a tempdir the harness staged. The
   harness reads the file back and validates against a Pydantic model
   (`WizardAnswers`, frozen + `extra="forbid"`, `version: Literal[1]`).
   If validation fails, the harness relaunches once (`--resume` plus a
   small `repair_prompt.txt` in the run dir). A second failure dumps
   whatever the subagent produced to
   `<data_dir>/.remory/wizard-recovery/<utc-iso>/` and aborts with a
   user-facing pointer at that directory.
3. **The COMMIT block.** Per-topic-atomic writes of `meta.yaml`,
   `state.md`, `CLAUDE.md`, plus the data-dir `about-me.md`. Preserved
   verbatim from Phase 5 (ADR-0003 leave-as-is on partial failure,
   ADR-0004 SIGINT deferral best-effort on Windows). The harness, not
   the subagent, is the only writer of user-visible files.

**`answers.json` wire format.** Pydantic, versioned. `version: 1` is
the forward-compat hook (memory `feedback_wire_format_enums`); bumping
the integer is forward-compatible; renaming the key or any Literal
value requires a migration plan analogous to `RawStatus`.

```json
{
  "version": 1,
  "name": "Sam",
  "chosen_topics": ["workout"],
  "knobs_by_topic": {
    "workout": {"tone": "warm", "strictness": "balanced"}
  },
  "wish": "stop forgetting"
}
```

`letter.md` is a sibling file; the subagent writes it after
`answers.json`. The harness embeds `letter.md`'s body as the first line
of `about-me.md`.

**One-shot repair, then recovery.** Two attempts total; no third. If
the model cannot produce valid JSON twice, the harness saves the
malformed output + `letter.md` + a `validation-error.txt` under a
timestamped recovery dir. Nothing the user said disappears silently
(memory `feedback_no_silent_data_loss`).

**`cwd=data_dir` is load-bearing.** The harness launches
`claude --agent wizard` with `cwd` set to the data directory root, NOT
a topic dir. The SessionEnd hook's "cwd not under topics/<name>/"
branch (the no_topic skip) is therefore the mechanism by which the
wizard's transcript is *not* captured as a raw entry. Documented as a
one-line addendum to ADR-0002.

## Consequences

- The Phase 5 modules `_steps.py`, `_letter.py`, `_validators.py` are
  deleted along with their unit tests
  (`test_wizard_letter.py`, `test_wizard_validators.py`,
  `test_wizard_three_strikes.py`). `WizardThreeStrikesError` is gone
  from the public surface (no replacement; the model handles "the
  user typed garbage" inside the conversation).
- New public exception types: `WizardPreflightError`,
  `WizardAnswerParseError`, `WizardSubagentFailedError`. The CLI's
  `format_error` table maps them to the new strings in `_strings.py`
  (preflight → `remory doctor` pointer, exit 2; subagent failed with
  recovery → recovery-dir message, exit 1).
- The wizard now hard-requires `claude` + auth. A user who runs
  `remory init` without `claude` on PATH sees the doctor pointer and
  exits 2 — they do not get a degraded fallback. Doctor's existing
  binary + auth checks are the recovery surface.
- The `Backend.chat` Protocol grows an `agent: str | None = None`
  kwarg. `chat_cmd` always passes `agent=None`; the wizard launch is
  the only `agent="wizard"` caller.
- `fake_claude` grows a `wizard_interactive` mode (env-driven; the
  test plants `FAKE_CLAUDE_WIZARD_ANSWERS` + optional
  `FAKE_CLAUDE_WIZARD_FAIL` variants and the fake writes the run-dir
  files). Real-claude smoke verification is a deferred PR-description
  checkbox.

## Alternatives considered

- **Keep the Python steps + delegate just the letter call.** The
  Phase 5 path. Rejected because the seam is the bug — the user feels
  two voices, not one. Adding more polish to the Python prompts
  doesn't close the gap; it widens the inconsistency.
- **Headless `claude -p` instead of an interactive session.** Rejected
  because the wizard is conversational; a headless single-shot would
  collapse the six beats into one prompt and lose the warm cadence.
- **Offline fallback (Python steps if claude is unreachable).**
  Rejected. The spec calls the model's voice load-bearing for §11; a
  silent degradation to a different UX surface would violate the
  "no silent degradation" principle. Better to refuse and point at
  `remory doctor` so the user fixes the precondition.
- **Free-form text answers (no Pydantic model).** Rejected. The wire
  surface needs a forward-compat hook (`version`) and adversarial
  input handling (`extra="forbid"`). Free-form prose between subagent
  and harness would silently absorb stray output instead of failing
  loudly enough to trigger the repair round.
- **Three-strikes repair (instead of one).** Rejected. The model
  rarely produces malformed JSON twice in a row; if it does, the
  problem is likely in the user's input (or the prompt template),
  and a third attempt won't help. The recovery dir is the better
  surface — the user can read what they said, fix the data dir, and
  re-run.

## References

- Phase 6 consolidated plan §6.1 (Pydantic wire surface), §6.2
  (`_subagent.py` API), §7 (orchestrator pseudocode), §11.1 (named
  unit tests), §12 (fake_claude wizard_interactive mode).
- ADR-0002 — chat vs. SessionEnd raw-write coordination; D4 in the
  Phase 6 plan adds a one-line addendum recording that the wizard's
  `cwd=data_dir` launch is the wizard-transcript skip mechanism. Do
  not move the wizard launch dir without re-reading ADR-0002.
- ADR-0003 — wizard commit partial-failure leave-as-is (preserved
  verbatim).
- ADR-0004 — wizard SIGINT Windows best-effort (preserved verbatim).
- Memory: `feedback_wire_format_enums` (forward-compat plan for
  Literal values in `answers.json`); `feedback_no_silent_data_loss`
  (recovery dir on two-strike validation fail);
  `feedback_silently_means_logged` (preflight refusal still emits a
  structured log).
