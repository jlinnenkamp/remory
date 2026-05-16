# ADR 0012: User data lives at $XDG_DATA_HOME/remory/, never inside the source tree

**Status:** Accepted. Foundational decision from build spec §2.

## Context

Remory writes meaningful user content to disk: the `about-me.md`
paragraph the wizard composes, the `state.md` for each topic, the raw
conversation transcripts under `raw/<year>/`, the `_review.md` from the
critic, and the timestamped state backups under `.backups/`. This is
not test fixture data; it is the user's evolving second brain. Where
on disk it lives is an architectural question with privacy and
developer-ergonomics consequences.

This ADR records the reasoning behind a decision that was settled in
`INSTRUCTIONS.md` §2 (the "Data directory" row), §3 (repository
layout), and §4 (data directory layout) rather than deliberated in a
PR. The data-directory location is locked; the Alternatives section
below does the real work of explaining why the rejected paths are
worse.

The decision: Remory resolves its data directory via `platformdirs`,
which yields `$XDG_DATA_HOME/remory/` on Linux (and the corresponding
platform-appropriate path on macOS and Windows). The directory is
strictly outside the source tree. `REMORY_DATA_DIR` exists as an
environment-variable override, used heavily by tests and available to
power users; the override does not relax the boundary — it relocates
it.

## Decision

`paths.py` exposes the canonical data-directory resolver. Resolution
order: `REMORY_DATA_DIR` environment variable if set; otherwise
`config.toml`'s `[paths] data_dir` if non-empty; otherwise
`platformdirs.user_data_path("remory")`. The platformdirs default is
the load-bearing piece — on every supported OS it places data outside
any plausible source-tree location, so the zero-config first-run never
collides with a developer's checkout.

The resolver refuses to return a path that is inside the Remory source
tree. The exact detection heuristic is a `paths.py` implementation
detail; the architectural rule is that the failure must be loud — a
startup error, not a warning — so developers see it immediately if they
try to point a data directory at their checkout. The override
(`REMORY_DATA_DIR` or `[paths] data_dir`) does not relax the boundary:
it relocates it, and the refusal still applies if the override resolves
to somewhere inside the repo.

**Mechanism (current).** `paths.refuse_if_inside_source_tree(candidate)`
discriminates by file layout: if `paths.py` itself is loading from a
`src/`-layout directory next to a `pyproject.toml`, any candidate that
resolves inside that directory raises `DataDirInsideSourceTreeError`.
Installed copies (pipx, pip into a venv) load `paths.py` from
`site-packages/` and so have no in-tree source to collide with — the
guard is a no-op for them. This paragraph documents the implementation
that ships today; the architectural rule above is what survives a
mechanism change.

Every code path that reads or writes user data routes through this
resolver. There is no fallback that writes "next to the binary" or
"into the current working directory." A user's data is in one place,
discoverable by `remory doctor`, and not co-located with the code that
operates on it.

## Consequences

Tests use `REMORY_DATA_DIR` set to a `tmp_path` directory; the
fake-`claude` test fixture (§12) follows the same rule. A test that
deliberately points the override at a path inside the source tree
fails loudly at the resolver — this is what catches the cases the
spec calls out as catastrophic (a contributor running the CLI from
their checkout and depositing transcripts into the working tree).
A test that simply omits the override falls through to the
platformdirs default, which lives outside the repo by construction,
so the floor is reachable even when fixtures forget the override.

The dev-time versus production-time `.claude/` distinction called out
in `CLAUDE.md` is a direct consequence of this rule. The `.claude/`
directory committed to the repo contains dev-time subagents (architect,
implementer, reviewer) used while building Remory. The `.claude/`
directory `remory init` materialises into the user's data directory
contains production-time subagents (extractor, merger, critic, wizard)
used while running Remory. They are two artefacts at two paths,
serving two audiences. The rule that user data lives outside the repo
is what makes this separation enforceable rather than merely
conventional.

A user backing up their data backs up `$XDG_DATA_HOME/remory/`. A user
versioning their data in a private git repository points that repo at
`$XDG_DATA_HOME/remory/` (the README's "Data and privacy" section
documents the `.gitignore` patterns). A user uninstalling Remory keeps
their data; a user wiping their data does not touch the install.

## Alternatives considered

- **A `data/` folder in the project root.** Catastrophic. Contributors
  who clone the repo and try the CLI — exactly the path the README
  encourages — would deposit personal conversation transcripts into
  the working tree. `git status` would surface them, `git add .` would
  stage them, and a careless commit would publish them. There is no
  configuration knob that prevents this once the default is set; the
  only safe default is one that cannot collide with the repo. We
  rejected this before the first line of code shipped, and we are
  documenting the rejection here so it stays rejected.
- **A `--data-dir <path>` flag as the only mechanism, with no default.**
  Rejected. The wizard, the first-run UX, depends on zero-config
  first-run: a user types `remory init` and the program decides where
  data lives. A required flag makes the first interaction a
  configuration chore rather than a conversation. The flag exists as
  an override (`REMORY_DATA_DIR`) for the tests and power users who
  need it, but the default path is what makes `remory init` feel like
  a product.
- **`~/.remory/` (a dotted home directory).** The pre-XDG convention.
  Rejected on portability and inspectability. XDG separates data
  (`$XDG_DATA_HOME`), config (`$XDG_CONFIG_HOME`), and state
  (`$XDG_STATE_HOME`) — Remory uses all three, with logs at
  `$XDG_STATE_HOME/remory/logs/` and config at
  `$XDG_CONFIG_HOME/remory/`. A single `~/.remory/` directory would
  conflate these into one path, and would render the data less
  discoverable to a user who already knows where modern Linux
  applications put their files. macOS users get the
  `~/Library/Application Support/remory/` placement automatically
  through `platformdirs`, which is the right macOS convention even
  though it disagrees with the XDG spec.

## References

- `INSTRUCTIONS.md` §2 (the "Data directory" row of the locked
  decisions table — `$XDG_DATA_HOME/remory/`, resolved via
  `platformdirs`, "Never inside the repo."), §3 (repository layout,
  which places no `data/` directory in the source tree), §4 (the
  full per-user data-directory layout this ADR governs), §9 (the
  `REMORY_DATA_DIR` env var and the `[paths] data_dir` config knob
  the resolver honours).
- `CLAUDE.md` — the dev-time versus production-time `.claude/`
  paragraph; the separation described there follows from this rule.
