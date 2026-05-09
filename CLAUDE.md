# CLAUDE.md — instructions for Claude Code working in this repository

You are helping build **Remory**, a terminal-based personal-assistant harness on top of Claude Code. The full specification is in `INSTRUCTIONS.md`. This file tells you *how to work*, not *what to build*.

## Your operating principles

1. **`INSTRUCTIONS.md` is the source of truth.** Read it end-to-end before doing anything. If it contradicts something I say in chat, ask which wins. If it contradicts something earlier in this file, ask.

2. **Follow the phased build order in §14 of `INSTRUCTIONS.md`.** Do not skip phases. Do not start phase N+1 before phase N tests are green.

3. **Plan before you code.** For every new task, produce a short written plan first. List files you'll touch, the tests you'll add, and any spec ambiguities you found. Wait for my approval before executing.

4. **Surface ambiguity loudly.** If the spec is unclear, ask. Do not improvise architecture. Do not invent features. If you are tempted to add something not in the spec, stop and ask.

5. **Tests with the change, not after.** Every code change in phases 1–6 ships with tests in the same PR. No "I'll add tests later." If a thing is hard to test, that's information about the design — surface it.

6. **Ruff, pyright, pytest must all be green** before declaring a phase complete. No exceptions.

## How to use the dev-time subagents

The `.claude/agents/` directory contains development-time subagents:

- **`architect`** — invoke when designing a new module's interface. Pass it the spec section and current code; it proposes the module structure.
- **`implementer`** — invoke for code-writing work once a design is settled. It works strictly to a written plan.
- **`reviewer`** — invoke after each phase to do a code-review pass against the spec checklist.

Use them. Don't try to do everything in the main thread. Each subagent has its own context window, so use them to keep the main conversation focused on planning and integration.

## Things you must not do

- Do not modify `INSTRUCTIONS.md` without explicit permission.
- Do not modify `state.md` files in production code paths outside the sleep pipeline. Section isolation is load-bearing.
- Do not write any user data into the source repo. The data directory is XDG-resolved and lives outside the repo.
- Do not add telemetry, analytics, crash reporting, or any phone-home behaviour. Zero is the right number.
- Do not introduce dependencies that aren't justified. The dependency list in `pyproject.toml` is reviewed.
- Do not bypass `ruff`, `pyright`, or `pytest` failures with `# noqa`, `# type: ignore`, or `pytest.skip` unless you've explained why in the same commit.
- Do not commit secrets, API keys, or `.env` files. Ever. Even temporarily.

## Things you should do

- Use **uv** for everything: `uv sync`, `uv add`, `uv run pytest`, `uv run ruff check`. Never `pip install` directly.
- Prefer **pathlib** over string paths everywhere.
- Prefer **Pydantic** for any structured data crossing a module boundary.
- Use **structured logging** via stdlib `logging`. No `print()` in library code; only in CLI surface code via `rich`.
- Write **type-hinted** code. `pyright` is in `strict` mode for `src/`.
- Use **descriptive commit messages**: imperative mood, references the phase, mentions the spec section. e.g. *"Phase 3: implement merge stage with section isolation (spec §7)"*.

## When you finish a piece of work

1. Run the full check: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest`.
2. If anything fails, fix it before declaring done.
3. Write a short summary of: what changed, what was tested, any spec ambiguities encountered, any TODOs left.
4. Wait for my review before moving to the next phase.

## CHANGELOG.md rule

`CHANGELOG.md` entries land **only when behaviour visible to a user changes** — new commands, new file formats, schema validation, error messages, anything an end user installing the released version would observe. Phases that ship purely internal infrastructure (tooling, refactors, dev-only test fixtures) **do not** get a CHANGELOG entry; the rationale lives in the phase commit body. Do not re-litigate this per phase. When in doubt, ask: "would a user installing this version notice this?" If no, no entry.

## On the relationship between dev-time and production-time `.claude/`

This repository's `.claude/` directory contains **dev-time** subagents and commands — for building Remory.

Remory itself, when installed and run by an end user, generates a *separate* `.claude/` directory inside the user's data directory containing **production-time** subagents (extractor, merger, critic, wizard) and slash commands and hooks. These are different concerns. Do not confuse them. Do not put production subagent definitions in this repo's `.claude/`; they live in `src/remory/` as templates and are written to the user's data dir at `remory init` time.

## Tone

When you talk to me about this work: direct, concise, opinionated. Push back on bad ideas, including mine. Surface uncertainty. Don't pad replies. The product is meant to feel warm; the development process is meant to feel sharp.
