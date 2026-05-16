# ADR 0009: Subprocess to the local claude CLI is the default backend

**Status:** Accepted. Foundational decision from build spec §2.

## Context

Remory needs an LLM driver. The obvious candidates are (a) drive the
user's existing local `claude` CLI as a subprocess, (b) call the
Anthropic Messages API directly via the SDK, or (c) build a custom
client over the SDK with no `claude` binary involved.

This ADR records the reasoning behind a decision that was settled in
`INSTRUCTIONS.md` §2 and §8 rather than deliberated in a PR. The
backend default is locked; the Alternatives section below does the real
work of explaining why the rejected paths are worse.

The decision: `ClaudeCodeBackend` is the default. It wraps the local
`claude` binary as a subprocess — `subprocess.run(["claude"], cwd=...)`
for interactive sessions, `claude -p ... --agent <name>` for headless
calls used by the sleep pipeline. `AnthropicAPIBackend` ships in `v0.1`
as a stub: an off-by-default reference implementation that requires
`ANTHROPIC_API_KEY` and is not exercised by CI. Backend selection is
one line of `config.toml` (`[backend] kind = "claude_code"` or
`"anthropic_api"`).

## Decision

The CLI advertises a single backend in its default install: the local
`claude` CLI. The choice is not just about which transport carries the
tokens. It is about which surface owns auth, billing, and the
subagent/hook machinery.

The backend protocol (`backends/base.py`, see §8) has three methods:
`chat(cwd, resume)` for interactive sessions, `headless(prompt, agent,
cwd, json_output, timeout_seconds)` for the sleep pipeline's calls, and
`health_check()` for `remory doctor`. `ClaudeCodeBackend` implements
each by shelling out to `claude` with the right flags. Transcript
capture (the chat surface's raw-entry source) reads from
`~/.claude/projects/<encoded-cwd>/*.jsonl` after the session ends — a
file the `claude` CLI is already writing for its own reasons.

`AnthropicAPIBackend` is a stub. It documents the protocol contract,
reads `ANTHROPIC_API_KEY`, and calls the Messages API. It is not
imported by default code paths and is not driven by CI. A contributor
implementing it past stub status is welcome to; a user pointing
`config.toml` at it gets metered API billing and loses the subagent and
hook surfaces, both of which are properties of Claude Code, not of the
underlying model.

## Consequences

Remory is dead in the water if `claude` is broken or unauthenticated.
This is not a hypothetical: it is the precondition the wizard's
preflight check exists to surface (ADR-0006), and it is the first
check `remory doctor` runs (§6). A user who installs Remory without
`claude` on `PATH` sees the doctor pointer and exits with a non-zero
code, not a degraded fallback. The wizard makes the same call. We
prefer the loud refusal to a silent UX downgrade.

The chat surface inherits whatever Claude Code is in the user's
environment. Model selection, slash commands the user has added,
custom hooks they have configured for their own use — all of that
applies. This is mostly good: it means a user already comfortable in
Claude Code finds Remory's chat surface familiar, and improvements to
Claude Code propagate to Remory without code changes. It is
occasionally awkward: Remory cannot pin a model version against a user
whose `claude` binary has just been updated. We accept the awkwardness;
the alternative is to fork the auth and config surfaces.

Billing flows through the user's Anthropic Max subscription. Remory
does not handle API keys for the default path, does not see token
counts, and does not need a billing surface. The `AnthropicAPIBackend`
escape hatch exists for users who prefer metered API and for
contributors who want to develop against the SDK directly; it is not
the recommended path.

## Alternatives considered

- **Make `AnthropicAPIBackend` the default.** Rejected. It splits the
  auth story (separate API key, separate billing surface from the rest
  of the user's Claude tooling) and forecloses on the wizard UX: the
  wizard relies on launching `claude --agent wizard` as a single
  conversational session, and that subagent + interactive surface does
  not exist outside the `claude` CLI. Choosing the API as default
  would require either reimplementing the subagent runtime in-process
  or dropping the conversational wizard, which we are not willing to
  do (see §11 and ADR-0006).
- **Build a custom client over the Anthropic SDK with no `claude`
  binary at all.** Rejected. The sleep pipeline depends on the
  subagent abstraction (extractor / merger / critic, each with its own
  system prompt and tool allowlist in `.claude/agents/`), and the chat
  surface depends on the hook abstraction (SessionEnd writes the raw
  entry; PreToolUse on `Edit`/`Write` protects `state.md`). Both of
  these are Claude Code primitives. Reimplementing them as a sibling
  runtime to call the SDK directly is months of work for an outcome
  worse than shelling out to the binary that already implements them.
- **Support both as first-class peers from day one, with CI matrix
  coverage across them.** Rejected for v0.1. The interface is real and
  documented (`Backend` protocol in `backends/base.py`), and the stub
  exists as a reference, but exercising both paths in CI doubles the
  surface that has to stay green for every PR. We prefer to keep
  `AnthropicAPIBackend` honest as a stub and revisit promotion past
  stub status when a real user need surfaces.

## References

- `INSTRUCTIONS.md` §2 (the locked decisions table — the "LLM backend
  (default)" and "LLM backend (stub)" rows), §6 (the `remory doctor`
  ordering that puts `claude` binary + auth before any topic check), §8
  (the `Backend` protocol contract and the two implementations), §10
  (the Claude Code subagent and hook surfaces this backend choice
  depends on).
- ADR-0006 — the wizard depends on this backend being live; its
  preflight check is the recovery surface when the precondition fails.
