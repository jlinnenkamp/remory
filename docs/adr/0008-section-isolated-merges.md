# ADR 0008: Section-isolated consolidation (one LLM call per section, never the whole state)

**Status:** Accepted (foundational).
**Date:** 2026-05-16.

## Context

Naive LLM memory systems weight recent inputs disproportionately. The
mechanism is unromantic: a single context window contains the whole
state plus the new inputs, and the model — pulled by the prompt's most
recent tokens — rewrites the old material in the colour of the new.
Across enough cycles the older context bleaches out. This is the
specific failure mode Remory exists to refuse.

This ADR records the reasoning behind a foundational design decision;
the merge step's shape is locked at the project level, not deliberated
per-PR. The Alternatives section below does the real work of explaining
why the rejected paths are worse.

The architectural answer Remory commits to: the merge step (§7 stage 2)
runs once per section. Each call's context window contains that
section's current text from `state.md`, the candidate updates routed to
that section by the extractor, the schema persona, and the per-topic
tone/strictness knobs. It contains nothing from any other section. The
model cannot drift across section boundaries because the other sections
are not in its context window.

## Decision

The merge stage in `sleep/orchestrator.py` iterates over the schema's
sections in order. For each non-`append_only` section that has
candidate updates, the orchestrator issues one headless backend call
whose prompt contains that section only. `append_only` sections (e.g.
`evidence_log`) bypass the LLM entirely and are appended to
mechanically.

This is a property of the orchestrator, not of the prompt. A prompt
that says "rewrite only section X" but ships the full `state.md` in
context does not satisfy this rule. The full state never appears in a
merge prompt. The critic (stage 3) is the only stage that sees the
whole state, and the critic cannot write to `state.md` — it writes only
to `_review.md`.

## Consequences

This is the load-bearing architectural property of the project.
`CONTRIBUTING.md` (Phase 7, Commit 3) names section isolation as the
one non-negotiable rule of the codebase. Any change to the merge
stage's shape — batching sections, sharing context across calls,
introducing a "global summary" pass that touches `state.md` — requires
a follow-up ADR superseding this one, and a corresponding spec
amendment.

The cost is real. One LLM call per section per sleep means a topic
with five sections pays five round trips during merge, plus extract
and (when configured) critique. We accept the cost because the
falsifiability property is what makes the design defensible: a
reviewer can read the merger's call site, confirm the prompt
construction, and assert by inspection that cross-section drift is
physically impossible. A "smart" merger that promises not to drift is
not the same artefact.

Backends must support cheap repeated small calls. `ClaudeCodeBackend`
does; the `AnthropicAPIBackend` stub does too. A future backend that
amortises only across batched calls would be incompatible with this
ADR.

## Alternatives considered

- **One merge call with sections delimited by headings in the prompt.**
  The naive shortcut. A single call receives the whole state, with the
  prompt instructing the model to rewrite section X without touching
  section Y. Rejected on two grounds. First, the model still drifts:
  recency bias is a property of the context window's contents, not of
  the instructions about them. Second — and worse — the project loses
  falsifiability. A reviewer cannot prove, by inspecting the call site,
  that drift did not happen on a given sleep. The product becomes "the
  model usually behaves." That is the failure mode of every memory
  system Remory exists to differ from.
- **Embedding-based section routing with a single merge pass.** Route
  candidate updates to sections by vector similarity, then merge in one
  call. Rejected on two grounds: it solves the wrong problem
  (extraction is already structured per section by the extractor; the
  merge step's locality is the architectural property under discussion)
  and it introduces a vector-database dependency v0.1 excludes (see
  "What v0.1 doesn't do" in `docs/architecture.md`). Vector recall is
  a different product;
  pulling it in to "improve" merge would launder a v0.3-territory
  dependency into v0.1 to fix a problem that section-isolated merging
  already solves.
- **Sequential merge with carry-over (section N's output visible to
  section N+1).** A halfway position: each section sees only itself,
  but reads the freshly merged previous sections from the working copy.
  Rejected because once any prior section's freshly rewritten text is
  in the context window, the order of section traversal becomes a
  hidden parameter of the output. Identical inputs produce different
  states depending on traversal order. Section isolation, as defined
  here, makes section-level merges commutative.

## References

- `CONTRIBUTING.md` — names section isolation as the project's one
  non-negotiable rule.
- `docs/architecture.md` "Section isolation" — the prose-level
  explanation of why this is shaped the way it is.
