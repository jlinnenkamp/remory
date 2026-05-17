# ADR 0006: Wizard rearchitected as a Claude Code subagent

**Status:** Accepted.
**Date:** 2026-05-14.

## Context

The wizard runs `remory init`'s first-time setup conversation. The
product contract is that this conversation has one voice — the
model's — and that the user feels they have met a warm, attentive
interlocutor rather than filled out a form.

An earlier implementation drove the wizard from Python: a linear
prompt loop, hand-written validators, a three-strikes counter on bad
input, and a single LLM call hoisted into the harness to compose a
"letter" paragraph. The UX worked, but two things were wrong with it:

1. **Two voices.** The Python prompts ("Pick one or more by number,
   separated by commas") and the model paragraph ("Hi Sam. I'll keep
   what you bring me here") read like two different products joined at
   the seam. The product contract asks for one voice, and that voice
   has to be the model's.
2. **The skip path was a form, not a conversation.** Every option
   question carried an `[s] Skip — use the default ("warm")` line. That
   is what an opinionated form looks like; it is not what a warm
   first-run interview looks like.

The harness should own three things only: making sure the session can
run, validating what the model writes back, and writing files
atomically. The conversation itself belongs to the model. This ADR
records the rewrite from the Python-driven stand-in to a single
`claude --agent wizard` session.

## Decision

The wizard is a single `claude --agent wizard` session. The
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
   `state.md`, `CLAUDE.md`, plus the data-dir `about-me.md`. The
   per-topic-atomic contract is governed by ADR-0003 (leave-as-is on
   partial failure) and ADR-0004 (SIGINT deferral, best-effort on
   Windows). The harness, not the subagent, is the only writer of
   user-visible files.

**`answers.json` wire format.** Pydantic, versioned. `version: 1` is
the forward-compat hook: bumping the integer is forward-compatible;
renaming the key or any Literal value requires a migration plan
analogous to `RawStatus`.

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
timestamped recovery dir. Nothing the user said disappears silently.

**`cwd=data_dir` is load-bearing.** The harness launches
`claude --agent wizard` with `cwd` set to the data directory root, NOT
a topic dir. The SessionEnd hook's "cwd not under topics/<name>/"
branch (the no_topic skip) is therefore the mechanism by which the
wizard's transcript is *not* captured as a raw entry. Documented as a
one-line addendum to ADR-0002.

## Consequences

- The earlier modules `_steps.py`, `_letter.py`, `_validators.py` are
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
  files).

## Alternatives considered

- **Keep the Python steps + delegate just the letter call.** The
  earlier path. Rejected because the seam is the bug — the user feels
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

- ADR-0002 — chat vs. SessionEnd raw-write coordination. The wizard's
  `cwd=data_dir` launch is the wizard-transcript skip mechanism — do
  not move the wizard launch dir without re-reading ADR-0002.
- ADR-0003 — wizard COMMIT partial-failure leave-as-is policy.
- ADR-0004 — wizard SIGINT Windows best-effort handling.
