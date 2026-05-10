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
import sys
from pathlib import Path
from typing import Annotated

import typer

from remory import config as cfgmod
from remory import paths
from remory.cli.errors import format_error
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
from remory.logging_setup import configure as configure_logging

__all__ = ["app", "main"]


_log = logging.getLogger("remory.cli")


app = typer.Typer(
    name="remory",
    help="Remory — a second brain that actually remembers.",
    no_args_is_help=True,
    add_completion=False,
)


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


def _emit_and_exit(exc: BaseException) -> None:
    """Print the user-facing error message and exit with the mapped code."""
    if isinstance(exc, KeyboardInterrupt):
        # CC8: print a newline so the next prompt isn't on the ^C line.
        sys.stderr.write("\n")
        raise typer.Exit(code=130) from exc
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


@app.command("init")
def cmd_init(
    topic_name: Annotated[str, typer.Argument(help="Topic name (lowercase, kebab/snake).")],
    schema: Annotated[
        str | None,
        typer.Option("--schema", help="Built-in schema: job-profile, workout, coaching."),
    ] = None,
) -> None:
    """Create a new topic from a built-in schema."""
    try:
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
