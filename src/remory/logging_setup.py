"""Logging configuration for the Remory CLI.

CLI commands route through :func:`configure` once at startup. Console
handler defaults to WARNING; ``--verbose`` lifts to INFO and ``--debug``
to DEBUG. The file handler at ``<state_dir>/logs/remory.log`` always
runs at DEBUG with size-based rotation, so post-mortem inspection of a
"silent" run still has the audit trail.

Logger naming convention: ``remory.<module>``. Library code uses
``logging.getLogger(__name__)``; CLI code goes through this configurer.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Literal

from remory import paths

__all__ = ["configure"]


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_FILE_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(process)d] [%(funcName)s]: %(message)s"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB
_BACKUP_COUNT = 3


Verbosity = Literal["warning", "info", "debug"]


def _level_for(verbosity: Verbosity) -> int:
    match verbosity:
        case "warning":
            return logging.WARNING
        case "info":
            return logging.INFO
        case "debug":
            return logging.DEBUG


def configure(
    *,
    verbosity: Verbosity = "warning",
    log_file: Path | None = None,
) -> Path | None:
    """Install the root handlers for a single CLI invocation.

    Idempotent across calls: any previously installed handlers on the
    ``remory`` logger are removed first so reconfiguring (e.g. tests)
    doesn't double-up.

    Returns the log file path actually used, or ``None`` if file logging
    was disabled (e.g. the logs directory could not be created).
    """
    root = logging.getLogger("remory")
    root.setLevel(logging.DEBUG)
    # Reset existing handlers so successive configure() calls don't pile up.
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setLevel(_level_for(verbosity))
    console.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(console)

    target_log = log_file if log_file is not None else paths.logs_dir() / "remory.log"
    try:
        target_log.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # If we can't create the logs dir, give up on file logging
        # silently — console logging still works. The CLI surface will
        # surface user-facing errors via the error mapping.
        return None

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            target_log,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        return None
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_LOG_FORMAT))
    root.addHandler(file_handler)

    # Quiet a noisy third-party logger that pyright won't complain about.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    return target_log
