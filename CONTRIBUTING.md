# Contributing to Remory

Remory is a small, opinionated project. The build specification in [`INSTRUCTIONS.md`](INSTRUCTIONS.md) is the source of truth for what goes in and what stays out, and the operating manual in [`CLAUDE.md`](CLAUDE.md) is the source of truth for how the work is done. Read both before opening a non-trivial PR.

## Section isolation — the one rule that is not negotiable

The merge stage of `remory sleep` runs one LLM call per section of `state.md`, and that call sees only that section's current text plus candidate updates routed to that section. **Do not** introduce a codepath, a prompt, or a refactor that lets the merger see another section. This is the load-bearing architectural property of the project; [ADR-0008](docs/adr/0008-section-isolated-merges.md) explains why. Reviewers will reject PRs that violate it, even if the change "looks cleaner".

## Dev setup

This project uses [uv](https://docs.astral.sh/uv/) for environment and dependency management. From a fresh clone:

```bash
uv sync                   # creates .venv and installs dev deps
uv run pytest             # runs the full suite against the fake claude
```

A real `claude` binary on PATH is **not** required for the test suite — `tests/fakes/fake_claude` is a Python script that mimics the CLI's flags and emits canned JSONL. If you want to exercise the real binary end-to-end (e.g. before tagging a release), install Claude Code and log it in; nothing else changes.

## Running the checks

A PR is ready when all four of these pass locally:

```bash
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest
```

CI runs the same four on Python 3.12 and 3.13, on Ubuntu and macOS. Do not use `# noqa`, `# type: ignore`, or `pytest.skip` to silence a failure without explaining why in the same commit.

## The fake-claude testing pattern

Tests that need to drive a conversation use the `fake_claude` script, placed first on `PATH` for the test session by a pytest fixture. The fake accepts the same flags real `claude` does (`-p`, `--agent`, `--output-format json`, `--resume`) and reads its scripted responses from a per-test fixture file. If you add a new code path that shells out to `claude`, extend the fake rather than mocking `subprocess.run` directly — the integration tests should exercise the real subprocess seam.

## Schema authoring

To add a new topic schema (built-in or user), see [`docs/schemas.md`](docs/schemas.md). The short version: YAML, sections declared in order, optional `append_only` per section, defaults for tone and strictness, and an optional `wizard_questions` block that the first-run wizard reads. Built-in schemas live in `src/remory/schemas_builtin/` and are reserved against user override; user schemas live in `$XDG_CONFIG_HOME/remory/schemas/`.

## Phased build order

The repository follows the phased build order in [`INSTRUCTIONS.md` §14](INSTRUCTIONS.md). v0.1 ships at the end of Phase 7. New features that do not fit the spec are not in scope for v0.1 — open an issue describing the use case and we can talk about a later version.

## Architecture decision records

Significant decisions go in `docs/adr/` as numbered markdown files. The foundational decisions from build spec §2 are recorded in ADRs 0008-0012; gap-driven decisions surfaced during the build are 0001-0007. If your PR changes a load-bearing property (anything in `INSTRUCTIONS.md` §2 or the section-isolation rule above), it needs an ADR. Look at [`docs/adr/0006-wizard-claude-driven-interview.md`](docs/adr/0006-wizard-claude-driven-interview.md) for the format.
