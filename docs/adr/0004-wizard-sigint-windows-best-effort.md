# ADR 0004: wizard SIGINT handling — POSIX strict, Windows best-effort

**Status:** Accepted.
**Date:** 2026-05-10.

## Context

Phase 5's wizard COMMIT block writes multiple files sequentially: per
chosen topic, `meta.yaml` + `state.md` + `CLAUDE.md`; then a single
`about-me.md` at the data-directory root. Each individual file write
goes through `remory.atomic.atomic_write_*`, which is itself atomic at
the inode level (sibling `.tmp` + `os.replace`).

The user can press Ctrl+C at any point. The wizard must:

1. **Pre-COMMIT** — leave no files behind. Standard `KeyboardInterrupt`
   propagation suffices because nothing has been written yet.
2. **Mid-COMMIT** — finish the in-flight `atomic_write_*` so we never
   leave a half-written `meta.yaml` or `state.md` (which would defeat
   the purpose of the atomic primitive: the user would be left with a
   broken topic dir and no recovery hint). Then surface "stopped
   mid-write" guidance pointing at `remory doctor`.

The mechanism for "finish the in-flight write before surfacing the
interrupt" is to mask SIGINT for the duration of the write block, then
unmask. POSIX provides this via `signal.pthread_sigmask`. Windows does
not.

## Decision

**POSIX:** use `signal.pthread_sigmask(SIG_BLOCK, [SIGINT])` on entry to
each per-write block, paired with `pthread_sigmask(SIG_UNBLOCK, …)` on
exit. The kernel queues any SIGINT delivered while masked; the queued
signal is delivered the moment we unmask, which raises
`KeyboardInterrupt` in the caller. Per-write granularity (one mask per
`atomic_write_*` / `write_meta` / `write_state` call) keeps the
window short — at most one file's I/O.

**Windows:** the wizard COMMIT block uses a flag-based handler
(`signal.signal(SIGINT, …)` setting a Python-level flag, with the
default handler reinstalled on exit). The flag is checked between
writes. This is **best-effort**: there is a known race window where a
SIGINT delivered during the body of a write — between the flag check
and the `os.replace` — will interrupt the write at an OS level rather
than being deferred. In practice the window is small (single-file fsync
+ rename), and Windows is a non-primary platform for v0.1.

## Consequences

- POSIX users get the strict guarantee: every started `atomic_write_*`
  completes before the wizard surfaces the interrupt. The user-facing
  message ("Stopped mid-write. Some files may exist. Run remory doctor
  to inspect.") is accurate.
- Windows users get the same UX in the common case, plus a small race
  window in which a partial `.tmp` file may be left behind. `remory
  doctor` already surveys orphan `.tmp` files (Phase 4), so the
  recovery surface is unchanged.
- A future phase that promotes Windows to primary-platform status will
  need to revisit. Candidates:
  - Use `SetConsoleCtrlHandler` directly (out-of-stdlib).
  - Move the COMMIT block into a worker thread; SIGINT only interrupts
    the main thread on Windows.
  - Reuse `subprocess`-based isolation (the wizard spawns a child for
    the write phase and waits for it).

## Alternatives considered

- **Single mask spanning the entire COMMIT block.** Rejected: a stuck
  fsync could leave the user unable to interrupt for arbitrarily long.
  Per-write granularity bounds the window to one file's I/O.
- **No deferral, document the partial-state recovery.** Rejected:
  `atomic_write_*` already guarantees individual files are atomic, but
  the wizard wants the boundary at the file level, not the
  `os.write` level. Without deferral, a SIGINT mid-write leaves a
  `.tmp` file behind that doctor can clean up — but the user has to
  notice and re-run, which is friction we can avoid on POSIX cheaply.
- **Threading-based deferral on POSIX.** Rejected: `pthread_sigmask`
  is the native primitive; emulating it via threads adds complexity
  for no gain.

## References

- ADR-0003 — partial-failure leave-as-is policy; doctor is the
  recovery surface for both partial-disk-failure and partial-Ctrl+C
  outcomes.
