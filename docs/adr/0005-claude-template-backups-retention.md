# ADR 0005: Claude template backups — flat layout, no v0.1 cleanup

**Status:** Accepted.
**Date:** 2026-05-14.

## Context

Phase 6 introduces `remory init --refresh`, which can overwrite files
under `<data_dir>/.claude/` (subagent and slash-command markdown,
`settings.json`) and per-topic `CLAUDE.md`. Whenever the refresher
overwrites a stamped-older or stamped-but-edited file (with `--force`),
it writes a `.bak` first so the user can recover anything they may have
authored or customised.

Three sub-decisions need pinning before the refresher ships:

1. Where do `.bak` files live?
2. When are they cleaned up?
3. How does the refresher know how to invoke itself from a hook
   (settings.json command line) on a clean PATH?

## Decision

**`.bak` layout — flat, under the data-dir-level `.claude/`.**

```
<data_dir>/.claude/.backups/<flattened-relpath>.<utc-iso-timestamp>.bak
```

Path components from the relative-to-`data_dir` source path are joined
with `__` (Windows-safe, easy to `ls`). The timestamp is UTC ISO with
colons replaced by hyphens (Windows-safe). Examples:

- `<data_dir>/.claude/.backups/agents__extractor.md.2026-05-12T14-23-07Z.bak`
- `<data_dir>/.claude/.backups/topics__workout__CLAUDE.md.2026-05-12T14-23-07Z.bak`

Per-topic `CLAUDE.md` backups go under the **same** data-dir-level
backups directory, not under each topic's own `.backups/`. The
wizard / refresher owns this space; per-topic `.backups/` is reserved
for the sleep pipeline (`state.md` rotation, Phase 3).

Writes go through `remory.atomic.atomic_write_bytes` — durability
discipline does not exempt the backup itself.

**Newer-on-disk template — warn but skip.**

If a markdown template on disk carries a `template_version=N` stamp
where `N > PRODUCTION_TEMPLATE_VERSION`, the refresher logs a WARNING
and skips the file in both default and `--force` modes. Rationale: a
downgraded `remory` binary refreshing an upgraded data dir would be
silently destructive. The user with a newer template either upgraded
`remory` and rolled it back (in which case they want their newer file
preserved) or the stamp is forged (in which case they get to handle
the WARNING manually). No silent overwrite in either case.

**No `.bak` cleanup in v0.1.** Backups accumulate. There is no time
ceiling, no count ceiling, no auto-prune. The refresher is invoked
manually and infrequently; even an aggressive user running
`--refresh --force` weekly will reach kilobytes of `.bak`, not
megabytes. Trade-off: simpler refresher; recoverability is unbounded
in time. A future `remory clean-backups` command is deferred to a
later phase.

**Hook command relies on bare `remory` on PATH.** The bundled
`settings.json` ships:

```json
"command": "remory _hook session-end"
```

— a bare command, expected to resolve via the user's PATH at hook
invocation time. The risk: if a user launches `claude` from a shell
without `remory` on PATH (e.g. a stripped systemd unit), the hook
silently fails to run. The escape hatch — a `--remory-cmd <abs>` flag
on `install_data_dir_templates` that pins the absolute path at install
time — is **deferred**. v0.1 ships with the bare-command form, and we
treat any field report as a signal to add the flag.

## Consequences

- Recovery is direct: the user `cp` from
  `<data_dir>/.claude/.backups/<flat>.<ts>.bak` to the target. No
  hidden subdirs.
- `ls <data_dir>/.claude/.backups/` is a single flat list, sortable by
  the timestamp at the end of each name.
- A `remory clean-backups` command is a tracked follow-up rather than
  shipped scaffolding nobody has needed yet.
- The bare-`remory` PATH assumption is a known footgun if any user
  reports issues; the fix is a single-flag patch, not a wire-format
  change.
- The newer-on-disk warn-and-skip preserves user data unconditionally
  against an accidental downgrade, at the cost of refusing to "fix"
  the file even when the user actually does want the older bundled
  bytes back (they would need to `rm` the stamp and re-run, or `rm`
  the file outright).

## Alternatives considered

- **Per-topic `.backups/` for `CLAUDE.md`.** Rejected — splits backup
  surface between sleep (per-topic) and wizard (data-dir-level) at
  the wrong axis. The wizard's backup is about its template, not
  about the topic's content; co-locating it with sleep's `state.md`
  rotation invites confusion when both directories fill at once.
- **Time-windowed retention (keep last 7 days).** Rejected for v0.1 —
  premature; we don't know what users do with `--refresh` yet.
- **Auto-resolve absolute `remory` path at install time.** Rejected
  for v0.1 — adds a wire-format detail (an absolute path baked into
  `settings.json`) that breaks on `pipx upgrade` or any move of the
  install location. Deferred until field reports demand it.

## References

- ADR-0001 — `os.fsync` baseline; atomic-write discipline applies to
  every disk write, backups included.
