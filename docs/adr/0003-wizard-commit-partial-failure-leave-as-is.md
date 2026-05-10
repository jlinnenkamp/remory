# ADR 0003: wizard COMMIT partial-failure policy

**Status:** Accepted. Decided in Phase 4. Implemented in Phase 5 wizard.

> Note on numbering: the consolidated Phase 4 plan refers to this as
> "ADR 0002". `0001` was already taken by the fsync-on-Darwin ADR
> (Phase 1a), so the chat ↔ SessionEnd ADR landed as `0002` and this
> one as `0003`.

## Context

The wizard COMMIT phase writes multiple topic directories sequentially.
If COMMIT fails partway (e.g. disk fills after topic A completes but
before topic B), the wizard must choose between rolling back topic A
(atomic-across-topics) or leaving it as-is (per-topic atomic).

## Decision

**Leave-as-is.** Each topic is independently atomic. Doctor is the
recovery surface.

User-facing message:

```
Stopped mid-write at topic '<name>'. Topic '<prior>' was created
successfully. Run remory doctor to inspect, or remory init <name> to
retry the failed topic.
```

Exit code 1.

## Consequences

- Each completed topic is independently valid, just lonely.
- User can run `remory init <name>` to add the failed topic later, or
  `remory doctor` to inspect.
- Rollback would require teardown logic with its own correctness
  surface and would violate the per-topic atomic-write contract.

## Alternatives considered

- **Atomic across all topics with rollback.** Rejected — teardown
  complexity, plus a teardown-of-teardown failure mode that doctor still
  has to surface.
- **Ship `remory init --retry-failed`.** Rejected as redundant — the
  same data outcome is reached by `remory doctor` + `remory init
  <single-topic>`, which is what users will reach for under the
  recommended message anyway.
