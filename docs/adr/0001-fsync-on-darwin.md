# ADR 0001: fsync semantics on Darwin

**Status:** Accepted.
**Date:** 2026-05-09.

**Decision.** `atomic.py` uses stdlib `os.fsync` only. We do not call `fcntl.F_FULLFSYNC` on Darwin in Phase 1.

**Context and gap.** On macOS, `os.fsync` flushes the kernel page cache to the storage device but does *not* instruct the device to flush its own hardware write cache. In a power-loss scenario between the rename and the device cache being flushed, an "atomically replaced" `state.md` may be lost or partially persisted. `fcntl.fcntl(fd, fcntl.F_FULLFSYNC)` is the macOS-specific call that closes this gap. It is platform-conditional and Phase 1 does not have a cross-platform fsync abstraction.

**Why deferred.** Remory is a single-user local tool. The realistic failure mode is process crash, not power loss; `os.fsync` plus atomic rename is sufficient against the former. Adding `F_FULLFSYNC` now means a platform branch in the lowest-level helper without a concrete durability requirement to test against.

**What would unblock revisiting.** A real durability requirement (e.g. shared NFS, sleep-during-save scenarios reported by users) or the introduction of a cross-platform fsync abstraction would justify revisiting. Either trigger reopens this ADR with a Phase-1.x or later proposal.
