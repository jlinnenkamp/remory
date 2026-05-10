"""``remory --version`` implementation.

Output format (CC4): ``remory <pep440-version>`` from
``importlib.metadata.version("remory")``. Exactly one line, no trailing
metadata.
"""

from __future__ import annotations

import importlib.metadata

__all__ = ["run_version"]


def run_version() -> str:
    """Return the version string to print (without trailing newline)."""
    try:
        version = importlib.metadata.version("remory")
    except importlib.metadata.PackageNotFoundError:
        version = "0.0.0+unknown"
    return f"remory {version}"
