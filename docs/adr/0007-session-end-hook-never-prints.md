# ADR 0007: SessionEnd hook never prints; the chat surface owns the nudge

**Status:** Accepted.
**Date:** 2026-05-14.

## Context

Two surfaces produce raw entries from a Claude Code chat session:

1. `remory chat` — the in-process canonical writer (ADR-0002).
2. The SessionEnd hook (Phase 6) — fires for *any* `claude` invocation,
   including direct ones outside `remory`.

Both can cross the per-topic "pending entries" threshold on the write
that completes the session. Phase 4 wired the threshold nudge into
`remory chat`'s stdout: a one-line `remory sleep <topic>` suggestion
when the threshold is met. Phase 6 lands the SessionEnd hook, which
faces the same crossing condition from a different process.

The naive policy ("whoever does the write prints the nudge") fails on
two cuts:

- **Two voices.** When the user runs `remory chat`, both the chat
  parent and the hook see the same `pending_count` after the write. If
  both print, the user sees the nudge twice.
- **Hook stdout is unsafe to lean on.** The hook runs inside `claude`'s
  process tree; its stdout is consumed by claude's hook protocol, not
  the user's terminal. Anything we print there either gets eaten or
  shows up in unexpected places. The chat parent owns the user's TTY.

## Decision

**The SessionEnd hook never prints the threshold nudge.** The nudge is
owned by `remory chat` and only `remory chat`. Users who invoke
`claude` directly (outside `remory chat`) will see the nudge on their
next `remory chat <topic>` invocation, when the chat surface notices
the pending count is already past threshold.

The hook still does everything else — captures the transcript, writes
the raw entry, bumps `pending_count` and `last_chat` — but it does not
write to stdout or stderr. Errors emit structured log records; the
user-visible surface is the chat command's nudge or `remory doctor`.

This decision interacts with ADR-0002:

- ADR-0002 fixes the **write-coordination** policy: chat-as-parent is
  canonical; the hook defers via `is_locked()` and a session-id scan.
- ADR-0007 (this) fixes the **stdout-coordination** policy: the chat
  surface owns the nudge; the hook is silent at the user-facing layer.

Together: when `remory chat` runs, it both writes the raw entry AND
prints the nudge; the hook fires post-`claude` and finds the lock held
(or, if the lock was released first, finds the session_id already on
disk) and skips silently. When the user invokes `claude` directly,
only the hook fires; it writes the raw entry, prints nothing, and the
user sees the nudge on their next `remory chat`.

## Consequences

- One voice: the user only sees the nudge in `remory chat`. No
  double-printing race.
- Direct `claude` users have a one-cycle nudge delay. Acceptable: the
  threshold-nudge is a hint, not an alarm.
- The hook's pure helper (`remory.hooks.session_end.run`) returns a
  structured outcome (`SessionEndOutcome`) which the `main()` shim
  ignores; the outcome is for tests and future tooling that wants to
  observe the hook's decisions. The shim's exit code is always 0 —
  hooks must not block claude.
- A unit test
  (`test_session_end_hook_never_prints_threshold_nudge_when_pending_crosses_threshold`)
  pins this contract: a threshold-crossing write via the hook produces
  no stdout. A second test
  (`test_chat_threshold_nudge_only_fires_in_chat_cmd_not_hook`)
  cross-pins both surfaces in one run.

## Alternatives considered

- **Both surfaces print, with a sentinel file to suppress the double.**
  Rejected — adds disk state for a UI concern; the sentinel itself
  would be a wire format with its own forward-compat plan.
- **Only the hook prints (chat surface stays silent).** Rejected —
  inverts ownership in the opposite direction. The chat surface is
  the user's primary entry point; hiding its threshold signal there
  in favour of a hook-only nudge would surface the nudge unreliably
  (the hook may not be installed; claude may eat the stdout).
- **Print the nudge from both, accept the double.** Rejected — the
  product's voice should be calm. One nudge is informative; two on
  the same write is noisy.

## References

- ADR-0002 — chat vs. SessionEnd raw-write coordination (load-bearing
  on the write side; this ADR is the stdout-side companion).
