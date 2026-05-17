"""Remory CLI — Typer app and root callback.

This package replaces what the consolidated plan called ``cli.py``: it
holds the Typer app and command callbacks here in ``__init__`` so the
``cli/errors.py`` sibling can live without colliding with a top-level
``cli.py``.

The plan listed ``cli.py`` and ``cli/__init__.py`` as separate files;
that is a single-namespace collision in Python. The implementation
collapses them into the package's ``__init__`` (which is what Typer
projects typically do anyway).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer

from remory import claude_assets, paths
from remory import config as cfgmod
from remory.cli.errors import TopicExistsError, format_error
from remory.commands import (
    chat_cmd,
    doctor_cmd,
    init_cmd,
    sleep_cmd,
    state_cmd,
    stats_cmd,
    topics_cmd,
    version_cmd,
)
from remory.commands import (
    ingest_cmd as ingest_cmd_mod,
)
from remory.commands import (
    recent_cmd as recent_cmd_mod,
)
from remory.commands import (
    review_cmd as review_cmd_mod,
)
from remory.hooks import app as _hook_app
from remory.logging_setup import configure as configure_logging
from remory.wizard import (
    WIZARD_REDIRECT_MESSAGE,
    WizardRedirectError,
    run_wizard,
)

__all__ = ["app", "main"]


_log = logging.getLogger("remory.cli")


app = typer.Typer(
    name="remory",
    help="Remory — a second brain that actually remembers.",
    no_args_is_help=True,
    add_completion=False,
)
# Phase 6: register the internal hook subapp. Hidden from --help; invoked
# only by the bundled .claude/settings.json on the user's data dir.
app.add_typer(_hook_app, name="_hook", hidden=True)


def _resolve_data_dir_or_exit() -> Path:
    """Resolve the effective data directory or exit with an error."""
    try:
        cfg = cfgmod.load_config()
    except cfgmod.ConfigError:
        # ConfigError is fine; resolve_data_dir falls back to defaults
        # for the *path*, but the load itself already failed and the
        # caller handles ConfigError separately. Use the env/XDG path.
        return paths.data_dir()
    return cfgmod.resolve_data_dir(cfg)


def _wipe_user_data(data_dir: Path) -> None:
    """Remove user-created state from ``data_dir`` for a clean re-init.

    Wipes ``topics/``, ``.remory/``, and ``about-me.md``. Leaves
    ``.claude/`` alone (templates are re-installable, and the next
    ``remory init`` re-installs them anyway). Idempotent: missing
    paths are skipped silently. Used by ``remory init --reset`` for
    testing fresh-install flows.

    Prints what was wiped to stdout so the user has a paper trail.
    """
    topics = data_dir / "topics"
    remory_scratch = data_dir / ".remory"
    about_me = data_dir / "about-me.md"

    wiped: list[str] = []
    if topics.exists():
        shutil.rmtree(topics)
        wiped.append("topics/")
    if remory_scratch.exists():
        shutil.rmtree(remory_scratch)
        wiped.append(".remory/")
    if about_me.exists():
        about_me.unlink()
        wiped.append("about-me.md")

    if wiped:
        sys.stdout.write(f"Reset: wiped {', '.join(wiped)} under {data_dir}\n")
    else:
        sys.stdout.write(f"Reset: nothing to wipe under {data_dir}\n")


def _emit_and_exit(exc: BaseException) -> None:
    """Print the user-facing error message and exit with the mapped code."""
    if isinstance(exc, KeyboardInterrupt):
        # CC8: print a newline so the next prompt isn't on the ^C line.
        sys.stderr.write("\n")
        raise typer.Exit(code=130) from exc
    # typer.Exit is the explicit "I already know my exit code, just exit"
    # signal. Re-routing it through format_error would convert legitimate
    # exit codes (doctor's 1 on failures, init's 2 on usage errors) into
    # the catch-all's "Something unexpected went wrong" / exit 99.
    if isinstance(exc, typer.Exit):
        raise exc
    data_dir = _resolve_data_dir_or_exit()
    message, code = format_error(exc, data_dir=data_dir)
    if message:
        sys.stderr.write(message)
        if not message.endswith("\n"):
            sys.stderr.write("\n")
    raise typer.Exit(code=code) from exc


# ---------------------------------------------------------------------------
# Root callback: --config / --verbose / --debug / --version
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        sys.stdout.write(version_cmd.run_version() + "\n")
        raise typer.Exit(code=0)


@app.callback()
def root(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to config.toml (replaces $REMORY_CONFIG_FILE)."),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="INFO-level logs to stderr.")
    ] = False,
    debug: Annotated[bool, typer.Option("--debug", help="DEBUG-level logs to stderr.")] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Print Remory version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Apply global flags before any subcommand runs."""
    if config is not None:
        # CC6: --config replaces $REMORY_CONFIG_FILE for this invocation.
        os.environ["REMORY_CONFIG_FILE"] = str(config)

    del version  # bound by Typer for the eager flag; consumed by the callback.
    verbosity = "debug" if debug else "info" if verbose else "warning"
    configure_logging(verbosity=verbosity)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# init --refresh renderer (plan §5.10 — column widths byte-pinned)
# ---------------------------------------------------------------------------


_ACTION_WIDTH = 10
_PATH_WIDTH = 30


def _refresh_rel_path(target: Path, data_dir: Path) -> str:
    """Return ``target`` relative to ``data_dir`` as a POSIX string."""
    try:
        rel = target.relative_to(data_dir)
    except ValueError:
        rel = target
    return rel.as_posix()


def _claude_subpath(rel: str) -> str:
    """Strip a leading ``.claude/`` from a relative path for display.

    The §5.10 example shows ``agents/extractor.md``, not
    ``.claude/agents/extractor.md`` — the "Refreshed .claude/" header
    already establishes the prefix.
    """
    if rel.startswith(".claude/"):
        return rel[len(".claude/") :]
    return rel


def _format_refresh_row(action: str, path: str, reason: str) -> str:
    """Render one §5.10 row: 2-space indent + action(10) + path(30) + (reason)."""
    return f"  {action.ljust(_ACTION_WIDTH)}{path.ljust(_PATH_WIDTH)}({reason})"


def _format_unchanged_row(count: int) -> str:
    """Render the §5.10 ``unchanged N file(s)`` summary row."""
    return f"  {'unchanged'.ljust(_ACTION_WIDTH)}{count} file(s)"


def _classify_refresh_entries(
    result: claude_assets.EmitResult, data_dir: Path
) -> tuple[list[str], list[str], int, list[str], list[str], int]:
    """Split a combined EmitResult into the two §5.10 blocks.

    Returns ``(claude_rows, claude_paths_for_unchanged, claude_unchanged,
    topic_rows, topic_paths_for_unchanged, topic_unchanged)`` where
    ``*_rows`` are pre-rendered §5.10 lines and ``*_unchanged`` are
    counts.

    Topic per-CLAUDE.md entries are identified by path prefix
    ``topics/<slug>/CLAUDE.md`` per plan §6.4.
    """
    claude_rows: list[str] = []
    topic_rows: list[str] = []
    claude_unchanged = 0
    topic_unchanged = 0
    # Track the actual paths kept-unchanged for callers that want to
    # double-check; today only the counts feed the output, but keeping
    # them around avoids re-iterating later.
    claude_unchanged_paths: list[str] = []
    topic_unchanged_paths: list[str] = []

    def _is_topic_claude_md(target: Path) -> bool:
        rel = _refresh_rel_path(target, data_dir)
        parts = rel.split("/")
        return len(parts) == 3 and parts[0] == "topics" and parts[2] == "CLAUDE.md"

    for target in result.written:
        rel = _refresh_rel_path(target, data_dir)
        if _is_topic_claude_md(target):
            topic_rows.append(_format_refresh_row("regenerate", rel, "missing"))
        else:
            claude_rows.append(_format_refresh_row("write", _claude_subpath(rel), "missing"))
    for target in result.overwritten:
        rel = _refresh_rel_path(target, data_dir)
        is_topic = _is_topic_claude_md(target)
        # claude_assets.refresh doesn't track the trigger that made each
        # overwrite happen (stamp-older vs knobs-changed vs stamped-but-
        # edited under --force), so we render a neutral reason here. The
        # earlier dry-run pass shows the per-file trigger via the
        # SkippedEntry path below — users who want the trigger detail
        # run `--refresh --dry-run` first.
        if is_topic:
            topic_rows.append(_format_refresh_row("regenerate", rel, ".bak saved"))
        else:
            claude_rows.append(_format_refresh_row("overwrite", _claude_subpath(rel), ".bak saved"))

    for entry in result.skipped:
        rel = _refresh_rel_path(entry.path, data_dir)
        is_topic = _is_topic_claude_md(entry.path)
        if entry.reason == "unchanged":
            if is_topic:
                topic_unchanged += 1
                topic_unchanged_paths.append(rel)
            else:
                claude_unchanged += 1
                claude_unchanged_paths.append(rel)
            continue
        if entry.reason == "unstamped-preserved":
            display = _format_refresh_row(
                "preserve",
                _claude_subpath(rel) if not is_topic else rel,
                "no stamp — likely user-authored",
            )
            (topic_rows if is_topic else claude_rows).append(display)
            continue
        if entry.reason == "stamped-but-edited":
            display = _format_refresh_row(
                "conflict",
                _claude_subpath(rel) if not is_topic else rel,
                "stamp current but file edited; --force to overwrite",
            )
            (topic_rows if is_topic else claude_rows).append(display)
            continue
        if entry.reason == "newer-on-disk":
            display = _format_refresh_row(
                "preserve",
                _claude_subpath(rel) if not is_topic else rel,
                "newer template version on disk; refusing to downgrade",
            )
            (topic_rows if is_topic else claude_rows).append(display)
            continue
        if entry.reason == "meta-malformed":
            topic_rows.append(
                _format_refresh_row(
                    "skip",
                    rel,
                    "meta.yaml malformed — re-run after fixing meta",
                )
            )
            continue
        # Unknown reason — surface verbatim rather than guess.
        (topic_rows if is_topic else claude_rows).append(
            _format_refresh_row(
                "skip",
                _claude_subpath(rel) if not is_topic else rel,
                entry.reason,
            )
        )

    return (
        claude_rows,
        claude_unchanged_paths,
        claude_unchanged,
        topic_rows,
        topic_unchanged_paths,
        topic_unchanged,
    )


def _format_refresh_output(
    result: claude_assets.EmitResult, data_dir: Path, *, dry_run: bool
) -> str:
    """Render the §5.10 user-visible output for one refresh pass.

    The format depends on (dry_run, anything-to-do) per the four cases
    in §5.10. Returns a single string ending in ``\\n``.
    """
    (
        claude_rows,
        _claude_unchanged_paths,
        claude_unchanged,
        topic_rows,
        _topic_unchanged_paths,
        topic_unchanged,
    ) = _classify_refresh_entries(result, data_dir)

    claude_root = data_dir / ".claude"
    claude_has_changes = bool(claude_rows)
    topic_has_changes = bool(topic_rows)
    any_changes = claude_has_changes or topic_has_changes

    lines: list[str] = []

    if not any_changes:
        # "nothing to do" case — same wording for dry-run and non-dry-run.
        total_topics = topic_unchanged
        lines.append(f".claude/ at {claude_root} is up to date.")
        lines.append(f"Per-topic CLAUDE.md is up to date for all {total_topics} topic(s).")
        return "\n".join(lines) + "\n"

    if dry_run:
        lines.append(f"Would update .claude/ templates at {claude_root}:")
    else:
        lines.append(f"Refreshed .claude/ templates at {claude_root}")
    for row in claude_rows:
        lines.append(row)
    if claude_unchanged > 0:
        lines.append(_format_unchanged_row(claude_unchanged))

    if dry_run:
        lines.append("Would update per-topic CLAUDE.md:")
    else:
        lines.append("Per-topic CLAUDE.md:")
    for row in topic_rows:
        lines.append(row)
    if topic_unchanged > 0:
        lines.append(_format_unchanged_row(topic_unchanged))

    if dry_run:
        lines.append("Run without --dry-run to apply (a .bak will be saved for each overwrite).")

    return "\n".join(lines) + "\n"


@app.command("init")
def cmd_init(
    topic_name: Annotated[
        str | None,
        typer.Argument(help="Topic name (lowercase, kebab/snake). Omit for the wizard."),
    ] = None,
    schema: Annotated[
        str | None,
        typer.Option("--schema", help="Built-in schema: job-profile, workout, coaching."),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Re-install bundled .claude/ templates and per-topic CLAUDE.md.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="With --refresh, overwrite stamped-but-edited files (.bak saved).",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="With --refresh, show planned actions without writing.",
        ),
    ] = False,
    reset: Annotated[
        bool,
        typer.Option(
            "--reset",
            help=(
                "Destructive: wipe topics/, .remory/, and about-me.md from the "
                "data dir before init. For testing fresh-install flows."
            ),
        ),
    ] = False,
) -> None:
    """Create a new topic, or refresh bundled assets.

    No args runs the first-run wizard. Pass a topic name plus --schema to
    create one topic non-interactively. --refresh re-installs the bundled
    .claude/ templates and per-topic CLAUDE.md. --reset wipes user data
    before init (testing helper, destructive).
    """
    try:
        if dry_run and not refresh:
            sys.stderr.write("--dry-run requires --refresh\n")
            raise typer.Exit(code=2)
        if reset and refresh:
            sys.stderr.write(
                "--reset wipes user data; --refresh only updates templates. Pick one.\n"
            )
            raise typer.Exit(code=2)
        if refresh:
            eff_data_dir = _resolve_data_dir_or_exit()
            eff_data_dir.mkdir(parents=True, exist_ok=True)
            result = claude_assets.refresh(eff_data_dir, force=force, dry_run=dry_run)
            sys.stdout.write(_format_refresh_output(result, eff_data_dir, dry_run=dry_run))
            return

        if reset:
            eff_data_dir = _resolve_data_dir_or_exit()
            _wipe_user_data(eff_data_dir)

        # Empty args → wizard. The orchestrator owns the data-dir
        # resolution, the interview, and the COMMIT block.
        if topic_name is None and schema is None:
            run_wizard()
            return

        if topic_name is None:
            # ``--schema`` given without a topic name. Typer makes
            # the argument optional, but logically we need a name.
            sys.stderr.write(
                "remory init: --schema requires a topic name.\n"
                "Try `remory init <name> --schema <schema>` "
                "or `remory init` for the wizard.\n"
            )
            raise typer.Exit(code=2)

        # R3 dispatch order: existing-topic check FIRST so a typo'd
        # name doesn't redirect through the wizard message.
        eff_data_dir = _resolve_data_dir_or_exit()
        target = eff_data_dir / "topics" / topic_name
        if target.exists():
            raise TopicExistsError(name=topic_name, topic_dir=target)

        if schema is None:
            raise WizardRedirectError(WIZARD_REDIRECT_MESSAGE)

        init_cmd.run_init(topic_name=topic_name, schema_name=schema)
    except (KeyboardInterrupt, Exception) as exc:
        _emit_and_exit(exc)


@app.command("chat")
def cmd_chat(
    topic_name: Annotated[str, typer.Argument(help="Topic to chat about.")],
    continue_session: Annotated[
        bool,
        typer.Option("--continue", help="Resume the most recent session for this topic."),
    ] = False,
) -> None:
    """Start an interactive Claude Code chat in the topic directory."""
    try:
        chat_cmd.run_chat(topic_name=topic_name, continue_session=continue_session)
    except (KeyboardInterrupt, Exception) as exc:
        _emit_and_exit(exc)


@app.command("sleep")
def cmd_sleep(
    topic_name: Annotated[str | None, typer.Argument(help="Topic to consolidate.")] = None,
    if_due: Annotated[
        bool,
        typer.Option("--if-due", help="Iterate over all topics; sleep only those at threshold."),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show proposed state.md without writing.")
    ] = False,
) -> None:
    """Consolidate pending raw entries into state.md."""
    try:
        sleep_cmd.run_sleep(topic_name=topic_name, if_due=if_due, dry_run=dry_run)
    except (KeyboardInterrupt, Exception) as exc:
        _emit_and_exit(exc)


@app.command("state")
def cmd_state(
    topic_name: Annotated[str, typer.Argument()],
) -> None:
    """Print state.md for a topic."""
    try:
        state_cmd.run_state(topic_name=topic_name)
    except (KeyboardInterrupt, Exception) as exc:
        _emit_and_exit(exc)


@app.command("recent")
def cmd_recent(
    topic_name: Annotated[str, typer.Argument()],
    n: Annotated[int, typer.Option("--n", min=1, max=50)] = 5,
) -> None:
    """List the last N raw entries for a topic."""
    try:
        recent_cmd_mod.run_recent(topic_name=topic_name, n=n)
    except (KeyboardInterrupt, Exception) as exc:
        _emit_and_exit(exc)


@app.command("review")
def cmd_review(
    topic_name: Annotated[str, typer.Argument()],
) -> None:
    """Print _review.md (last critique output) for a topic."""
    try:
        review_cmd_mod.run_review(topic_name=topic_name)
    except (KeyboardInterrupt, Exception) as exc:
        _emit_and_exit(exc)


@app.command("ingest")
def cmd_ingest(
    topic_name: Annotated[str, typer.Argument()],
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
) -> None:
    """Ingest a markdown file as a raw entry (source: ingested)."""
    try:
        ingest_cmd_mod.run_ingest(topic_name=topic_name, file=file)
    except (KeyboardInterrupt, Exception) as exc:
        _emit_and_exit(exc)


@app.command("topics")
def cmd_topics() -> None:
    """List configured topics."""
    try:
        topics_cmd.run_topics()
    except (KeyboardInterrupt, Exception) as exc:
        _emit_and_exit(exc)


@app.command("stats")
def cmd_stats() -> None:
    """Cross-topic stats: entries, last sleep, simple streaks."""
    try:
        stats_cmd.run_stats()
    except (KeyboardInterrupt, Exception) as exc:
        _emit_and_exit(exc)


@app.command("doctor")
def cmd_doctor(
    strict: Annotated[
        bool, typer.Option("--strict", help="Add the non-canonical state.md check.")
    ] = False,
    probe_real_cli: Annotated[
        bool,
        typer.Option(
            "--probe-real-cli",
            help="Add a one-shot path-encoding probe (costs an LLM call).",
        ),
    ] = False,
) -> None:
    """Run health checks against the data directory and the claude binary."""
    try:
        doctor_cmd.run_doctor(strict=strict, probe_real_cli=probe_real_cli)
    except (KeyboardInterrupt, Exception) as exc:
        _emit_and_exit(exc)


def main() -> None:
    """Entry-point for ``python -m remory`` and the ``remory`` script."""
    app()
