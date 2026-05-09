#!/usr/bin/env python
"""Standalone subprocess that acquires topic_lock and blocks on stdin.

Used by tests/conftest.py::multi_process_lock_holder fixture to honestly
test cross-process flock behaviour. Threads share file descriptors and
would lie; only multi-process tests are honest about flock cross-process
contracts.
"""

from __future__ import annotations

import sys
from pathlib import Path

from remory.locking import topic_lock


def main() -> int:
    topic = Path(sys.argv[1])
    with topic_lock(topic):
        sys.stdout.write("LOCKED\n")
        sys.stdout.flush()
        sys.stdin.read()  # blocks until stdin closes
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
