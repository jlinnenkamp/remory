"""Remory — a second brain that actually remembers."""

from __future__ import annotations


def main() -> None:
    """Entry-point for the ``remory`` console script.

    Defined as a thin shim so :mod:`pyproject.toml`'s
    ``[project.scripts]`` mapping (``remory = "remory:main"``) resolves
    without import-time side effects from the Typer app module.
    """
    from remory.cli import app

    app()
