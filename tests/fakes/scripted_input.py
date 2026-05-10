"""List-of-lines test input wrapper for the wizard's prompt seam.

Replaces stdlib ``input()`` with a deterministic queue of canned
lines. Used by the wizard unit + integration tests via the
``input_fn=...`` test seam on :func:`remory.ui.prompt_line` (and the
public Phase 4 :func:`remory.ui.prompt_text`).

Usage:

.. code-block:: python

    fake = ScriptedInput(["Sam", "1,2", "1", "1", "1", "1", "stop forgetting"])
    answers = run_wizard(input_fn=fake)

Behaviour:

- Each call to the instance returns the next line from the queue.
- ``EOFError`` when the queue is exhausted (matches stdin EOF).
- An optional ``raise_at`` index triggers a single
  :class:`KeyboardInterrupt` at that 0-indexed call (after the prior
  lines have been served and *before* the line at that index would
  have been served).
"""

from __future__ import annotations

__all__ = ["ScriptedInput"]


class ScriptedInput:
    """Callable that pops lines from a list and feeds them to the wizard."""

    def __init__(
        self,
        lines: list[str],
        *,
        raise_at: int | None = None,
    ) -> None:
        self._lines = list(lines)
        self._idx = 0
        self._raise_at = raise_at

    def __call__(self) -> str:
        if self._raise_at is not None and self._idx == self._raise_at:
            # Single-shot: clear the trigger so subsequent calls
            # don't loop into the same interrupt.
            self._raise_at = None
            raise KeyboardInterrupt
        if self._idx >= len(self._lines):
            raise EOFError("ScriptedInput exhausted")
        line = self._lines[self._idx]
        self._idx += 1
        return line

    @property
    def lines_consumed(self) -> int:
        return self._idx
