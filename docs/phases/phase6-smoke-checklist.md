# Phase 6 — deferred real-CLI smoke checklist

Phase 6 ships with two real-CLI smoke verifications that cannot run in
CI (no real `claude` on PATH, no real Anthropic credentials). The
implementer commits them as unchecked boxes; the user runs them
manually after the phase lands. The file is a tracking artefact, not a
gate.

Source: consolidated plan §13.

- [ ] `claude --agent wizard` runs interactively without error on a clean data dir.
- [ ] A captured-stdin sample from a real `claude` SessionEnd invocation matches the hook parser's expected payload shape.
