# Phase 7 — install verification checklist

Phase 7 closes v0.1; this file tracks the install-path verification spec
§14 asks for. The file is a tracking artefact, not a CI gate. Tier 0
(pre-flight) runs as part of the PR work; Tiers A–C run on machines
this dev environment does not have access to.

Source: Phase 7 plan §4 (install verification).

## Tier 0 — pre-flight via isolated venv (done; this commit)

The dev environment that produced this commit does not have `pipx`
installed. As a substitute, an isolated `python -m venv` plus a
local-path `pip install` exercises the same surfaces — wheel build,
isolated install, entry-point resolution (`remory = "remory:main"`),
binary on `PATH`. Tier A still needs the genuine `pipx install` flow,
but the pre-flight catches the same install-time bugs and provides a
reference output for Tier A reviewers.

Commands run:

```bash
python3 -m venv /tmp/remory-tierA/venv
/tmp/remory-tierA/venv/bin/pip install .
REMORY_DATA_DIR=/tmp/remory-tierA/data \
REMORY_STATE_DIR=/tmp/remory-tierA/state \
  /tmp/remory-tierA/venv/bin/remory doctor
```

- [x] `pip install` of the local checkout exits 0.
- [x] `remory --version` prints `remory 0.1.0`.
- [x] `remory --help` lists every command from spec §6:
  `init`, `chat`, `sleep`, `state`, `recent`, `review`, `ingest`,
  `topics`, `stats`, `doctor` (10 commands).
- [x] `remory doctor` on a virgin `REMORY_DATA_DIR` + `REMORY_STATE_DIR`
  reports the seven baseline checks, fails the
  `claude templates` row with the locked remediation pointer
  (`-> run \`remory init\` to recreate`), and exits 1 cleanly.

## Bug surfaced during Tier 0 (fixed, this commit)

`remory doctor` was exiting 99 with `Something unexpected went wrong:
Exit().` after printing its full report. Root cause: `_emit_and_exit`
caught `typer.Exit` alongside other `Exception` subclasses and re-routed
it through `format_error`'s catch-all, converting doctor's legitimate
`typer.Exit(code=1)` into the bug-report banner. The fix adds a
`typer.Exit` propagation branch in `_emit_and_exit` so the explicit
"I already know my exit code" signal passes through unchanged.

Regression test: `tests/integration/test_doctor_e2e.py::
test_doctor_cli_exits_with_code_1_on_failure_not_99_via_catchall`.

The same bug shadow-affected `remory init` validation paths that
`raise typer.Exit(code=2)` for usage errors. Those now also exit 2
cleanly. The defensive workaround at `cli/__init__.py:412-414`
(validate flag combination before the try block) is now redundant
but harmless; left in place.

## Tier A — `pipx install git+...` from a real shell (user runs)

**Tier A is a release-blocker for v0.1.0 tagging; do not push the
v0.1.0 tag without it.** Spec §13's literal ask is "verify
`pipx install git+...` works from a clean machine"; Tier 0's
`python -m venv` substitute exercises the same surfaces but does not
exercise pipx itself.

Requires `pipx` installed on the host. Run on Linux and macOS.

```bash
pipx install git+https://github.com/jlinnenkamp/remory.git
remory --version
remory --help
REMORY_DATA_DIR=/tmp/remory-tierA-real remory doctor
pipx uninstall remory
```

- [ ] `pipx install` exits 0.
- [ ] `remory --version` prints `remory 0.1.0`.
- [ ] `remory --help` lists the 10 commands above.
- [ ] `remory doctor` on a virgin data dir exits 1 cleanly (no
  `Something unexpected went wrong` message). On a host with `claude`
  installed, the FAIL row is `claude templates` (no `.claude/`
  templates materialised yet); on a host without `claude`, the FAIL
  row is `claude binary` (no binary on PATH). Both are correct
  first-run states.
- [ ] `pipx uninstall remory` exits 0 and `remory` is no longer on PATH.

## Tier B — Docker container with fresh Python 3.12 (user runs)

One-off; not wired into CI for v0.1. Verifies the install path against
a clean Python and clean `pipx` install. Linux only.

```bash
docker run --rm -it python:3.12-slim bash -lc '
  pip install --user pipx &&
  ~/.local/bin/pipx install git+https://github.com/jlinnenkamp/remory.git &&
  ~/.local/bin/remory --version &&
  ~/.local/bin/remory --help
'
```

- [ ] Container build/start succeeds with no host contamination.
- [ ] `pipx install` exits 0.
- [ ] `remory --version` prints `remory 0.1.0`.
- [ ] `remory --help` lists the 10 commands above.

## Tier C — clean VM, Linux + macOS (user runs)

Spec §13's "verify `pipx install git+...` works from a clean machine."
Run on a freshly-provisioned VM (or freshly-imaged laptop) that has
Python 3.12+ and `pipx` and nothing else from this project.

### Linux

- [ ] `pipx install git+...` exits 0.
- [ ] `remory --version` prints `remory 0.1.0`.
- [ ] `remory init` walks the wizard against a real `claude` install
  and writes the expected on-disk artefacts.
- [ ] `remory chat <topic>` and `remory sleep <topic>` round-trip
  against the real `claude` CLI.

### macOS

- [ ] `pipx install git+...` exits 0.
- [ ] `remory --version` prints `remory 0.1.0`.
- [ ] Data directory resolves under `~/Library/Application Support/remory/`.
- [ ] `remory chat <topic>` and `remory sleep <topic>` round-trip
  against the real `claude` CLI.
