# ADR 0002: chat ↔ SessionEnd raw-write coordination

**Status:** Accepted. Decided in Phase 4. Implemented in Phase 4 (chat parent always writes); Phase 6 ships the deferring hook.

> Note on numbering: the consolidated Phase 4 plan refers to this as "ADR
> 0001". `0001` was already taken by the fsync-on-Darwin ADR (Phase 1a),
> so we land this as `0002` and the wizard COMMIT partial-failure ADR as
> `0003`. The numbering shift is mechanical; no content changes.

## Context

Two surfaces produce raw entries from a Claude Code chat session:

1. `remory chat` — in-process, after the subprocess exit.
2. The SessionEnd hook (Phase 6) — out-of-process, fired by `claude` for
   *any* invocation of `claude`, including direct ones outside `remory`.

Phase 2 shipped shared helpers (`transcripts.to_markdown`, `raw.write_raw`)
without committing to which surface owns the write. Under fork+wait
(see "chat lock mechanics" in the plan), the chat parent holds
`topic_lock` continuously across the subprocess; the hook is invoked by
`claude` as a separate process with no shared environment.

## Decision

**Chat-as-parent is canonical writer.** The Phase 6 hook script defers via
`locking.is_locked(topic_dir)` non-blocking probe at hook entry; if held,
the hook skips and exits 0 silently (debug log only). When the hook
acquires the lock, it scans `list_raw(topic_dir, status=None)` for an
existing raw entry with the same `frontmatter.session_id` as a
belt-and-suspenders idempotency floor before writing.

`chat_cmd.py` does **not** branch on hook presence — it always writes.

## Consequences

- The chat-parent crash window (post-exit, pre-write) is *covered* by
  the hook acting as safety net — when Phase 6 ships.
- The hook crash window has no recovery path. Accepted risk.
- Phase 4 ships a working chat with no hook; direct `claude` invocations
  outside `remory chat` produce no raw entry until Phase 6. Phase 4
  doctor surfaces this with an INFO line.
- The `is_locked()` probe + session-id scan combination removes any need
  for env-var plumbing or sentinel files.

## Alternatives considered

- **(a) Hook canonical, chat detects via session-id scan and skips.**
  Rejected — inverts ownership: chat is the user-facing surface and
  should not skip its own write on a hook race.
- **(c) Symmetric idempotent writes via session-id-keyed sentinel.**
  Rejected — symmetry is illusory because the surfaces have asymmetric
  context (chat holds the lock; the hook does not by default).
