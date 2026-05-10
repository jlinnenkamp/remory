"""Module entry point for ``python -m remory``."""

from __future__ import annotations

from remory.cli import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
