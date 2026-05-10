"""Command implementations for the Remory CLI.

Each module exposes a single ``run_<name>`` entry point that the Typer
callback in :mod:`remory.cli` invokes. Errors raised by these functions
are mapped to user messages by :mod:`remory.cli.errors`.
"""
