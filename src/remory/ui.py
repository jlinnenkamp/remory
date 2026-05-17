"""Terminal UI primitives for the Remory CLI surface.

Pure rendering and prompt helpers shared by :mod:`remory.commands.*`. No
file I/O, no domain decisions: callers compose this with the data layer.

Output policy:

- TTY-aware glyphs (`OK ✓`, `WARN !`, `FAIL ✗`, `SKIP ·`, `INFO i`) when
  stdout is a TTY and color isn't disabled; ASCII fallback (``ok``,
  ``warn``, ``fail``, ``skip``, ``info`` 4-char right-padded) otherwise.
- Color via ``rich.console.Console``; disabled when ``not isatty()``,
  ``--no-color``, ``NO_COLOR`` env var, or ``ui.colour = "never"`` in
  config.
- Narrow terminals (``COLUMNS < 60``) get a plain-text fallback: same
  text, no centring or wrapping decoration.

The R4 sleep-output critique-skip note is **locked verbatim**; see
``print_sleep_summary``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import IO, Literal

from rich.console import Console

from remory.config import Config
from remory.sleep.orchestrator import SleepResult, SleepStatus

__all__ = [
    "CheckResult",
    "CheckStatus",
    "is_narrow",
    "is_tty",
    "make_console",
    "print_doctor_report",
    "print_sleep_summary",
    "print_topics_table",
    "prompt_choice",
    "prompt_line",
    "prompt_text",
    "use_color",
]


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def is_tty(stream: IO[str] | None = None) -> bool:
    """True iff ``stream`` (default ``sys.stdout``) is attached to a TTY."""
    s = stream if stream is not None else sys.stdout
    try:
        return bool(s.isatty())
    except (AttributeError, ValueError):
        return False


def is_narrow(columns: int | None = None) -> bool:
    """True iff the terminal is narrower than 60 columns.

    ``columns=None`` reads ``$COLUMNS`` (then ``shutil.get_terminal_size``).
    """
    if columns is not None:
        return columns < 60
    env = os.environ.get("COLUMNS")
    if env:
        try:
            return int(env) < 60
        except ValueError:
            pass
    # Fall back to terminal size; default is 80x24 which is wide enough.
    import shutil

    size = shutil.get_terminal_size(fallback=(80, 24))
    return size.columns < 60


def use_color(cfg: Config | None = None, *, stream: IO[str] | None = None) -> bool:
    """Decide whether to emit ANSI colour escapes.

    Precedence: ``NO_COLOR`` env (any value) disables; ``cfg.ui.colour``
    ``"never"`` disables, ``"always"`` forces on; otherwise auto =
    ``is_tty(stream)``.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if cfg is not None:
        match cfg.ui.colour:
            case "never":
                return False
            case "always":
                return True
            case "auto":
                pass
    return is_tty(stream)


def make_console(cfg: Config | None = None, *, stderr: bool = False) -> Console:
    """Build a :class:`rich.Console` honouring the config + TTY rules."""
    stream = sys.stderr if stderr else sys.stdout
    color_system: Literal["auto"] | None = "auto" if use_color(cfg, stream=stream) else None
    return Console(
        file=stream,
        force_terminal=False,
        color_system=color_system,
        no_color=color_system is None,
        highlight=False,
        soft_wrap=True,
    )


# ---------------------------------------------------------------------------
# Doctor report rendering
# ---------------------------------------------------------------------------


class CheckStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"
    INFO = "info"


@dataclass(frozen=True)
class CheckResult:
    """One doctor check row.

    ``id`` is a stable identifier (e.g. ``"data_dir"`` or
    ``"topic:job-profile"``) used for testing. ``label`` is the
    human-readable column 2 text. ``detail`` is the right-hand value.
    ``remediation`` lines (each prefixed with ``-> `` in the rendered
    output) describe how to fix when status != OK.
    """

    id: str
    status: CheckStatus
    label: str
    detail: str
    remediation: tuple[str, ...] = ()


_GLYPH_TTY: dict[CheckStatus, str] = {
    CheckStatus.OK: "OK   ",
    CheckStatus.WARN: "WARN ",
    CheckStatus.FAIL: "FAIL ",
    CheckStatus.SKIP: "SKIP ",
    CheckStatus.INFO: "INFO ",
}

_GLYPH_ASCII: dict[CheckStatus, str] = {
    CheckStatus.OK: "ok  ",
    CheckStatus.WARN: "warn",
    CheckStatus.FAIL: "fail",
    CheckStatus.SKIP: "skip",
    CheckStatus.INFO: "info",
}

# ANSI escapes for the colored statuses. SKIP and INFO stay uncolored so
# the eye lands on the rows that need attention.
_ANSI_RESET = "\033[0m"
_ANSI_COLOR: dict[CheckStatus, str] = {
    CheckStatus.OK: "\033[32m",  # green
    CheckStatus.WARN: "\033[33m",  # yellow
    CheckStatus.FAIL: "\033[31m",  # red
}


def _glyph(status: CheckStatus, *, color: bool) -> str:
    if not color:
        return _GLYPH_ASCII[status]
    glyph = _GLYPH_TTY[status]
    ansi = _ANSI_COLOR.get(status)
    return f"{ansi}{glyph}{_ANSI_RESET}" if ansi is not None else glyph


def render_doctor_report(
    *,
    results: list[CheckResult],
    color: bool,
    label_width: int = 16,
) -> str:
    """Render the doctor report to a single string.

    Pure: no I/O. The renderer separates remediation lines with the
    ``-> `` indicator on a new line under the failing check. Topic
    rows (id starting with ``"topic:"``) are visually grouped after
    the global checks via a single blank line.
    """
    lines: list[str] = []
    lines.append("remory doctor")
    lines.append("================================================================")
    lines.append("")

    saw_topic = False
    for res in results:
        if res.id.startswith("topic:") and not saw_topic:
            lines.append("")
            saw_topic = True
        glyph = _glyph(res.status, color=color)
        # Two-space indent matches §4 examples. Pad label to label_width.
        label_padded = res.label.ljust(label_width)
        lines.append(f"  {glyph} {label_padded} {res.detail}".rstrip())
        for rem in res.remediation:
            lines.append(f"       -> {rem}")

    lines.append("")
    counts = {
        "ok": sum(1 for r in results if r.status is CheckStatus.OK),
        "warn": sum(1 for r in results if r.status is CheckStatus.WARN),
        "fail": sum(1 for r in results if r.status is CheckStatus.FAIL),
        "skip": sum(1 for r in results if r.status is CheckStatus.SKIP),
        "info": sum(1 for r in results if r.status is CheckStatus.INFO),
    }
    # Footer count matches rows shown (D9 reconciliation): include INFO
    # rows in the total, since they are checks the user sees.
    total = len(results)
    warn = counts["warn"]
    fail = counts["fail"]
    if fail == 0 and warn == 0:
        footer = f"{total} checks, 0 warnings, 0 failures. You're good."
    elif fail == 0:
        plural_warn = "warning" if warn == 1 else "warnings"
        footer = (
            f"{total} checks, {warn} {plural_warn}, 0 failures. "
            "Worth fixing the warnings before sleeping."
        )
    else:
        plural_warn = "warning" if warn == 1 else "warnings"
        plural_fail = "failure" if fail == 1 else "failures"
        footer = (
            f"{total} checks, {warn} {plural_warn}, {fail} {plural_fail}. "
            "Fix the failures before sleeping."
        )
    lines.append(footer)
    return "\n".join(lines) + "\n"


def print_doctor_report(
    results: list[CheckResult],
    *,
    cfg: Config | None = None,
    console: Console | None = None,
) -> None:
    """Render and print the doctor report to ``console`` (default stdout)."""
    color = use_color(cfg)
    text = render_doctor_report(results=results, color=color)
    c = console if console is not None else make_console(cfg)
    # Use ``c.out`` to bypass rich markup parsing; we rendered plain text.
    c.out(text, end="")


# ---------------------------------------------------------------------------
# Sleep summary rendering (R4)
# ---------------------------------------------------------------------------


# R4 (locked verbatim): when SleepResult.status == SUCCESS_WITH_WARNINGS and
# the critique step couldn't write _review.md, sleep output ends with this
# italic note. This is a sleep-output normal path, NOT an error path. Do
# NOT edit this text without updating the consolidated plan §5.
_R4_CRITIQUE_SKIP_NOTE = (
    "note: critique step couldn't run; state.md is up to date but _review.md\nwasn't refreshed."
)


def render_sleep_summary(result: SleepResult) -> str:
    """Render a :class:`SleepResult` to its CLI summary text.

    Pure: no I/O. Tested in
    ``test_print_sleep_summary_success_with_warnings_critique_skip_renders_locked_note``.
    """
    lines: list[str] = []

    if result.status == SleepStatus.NO_PENDING:
        lines.append(f"Nothing pending for '{result.topic_name}'. Nothing to do.")
        return "\n".join(lines) + "\n"

    n = result.consolidated_count
    word = "entry" if n == 1 else "entries"
    lines.append(f"Consolidated {n} pending {word} for '{result.topic_name}'.")
    if result.backup_path is not None:
        lines.append(f"Backup: {result.backup_path}")
    if result.review_path is not None:
        lines.append(f"Review: {result.review_path}")

    # Drift-drop notes from SleepResult.notes render with a "note: " prefix.
    drift_notes = [n for n in result.notes if n.startswith("dropped drift section")]
    for note in drift_notes:
        lines.append(f"note: {note}")

    # R4: SUCCESS_WITH_WARNINGS with no review_path means critique was
    # skipped or failed; render the locked italic note.
    critique_failed = any(n.startswith("critique failed") for n in result.notes)
    if (
        result.status == SleepStatus.SUCCESS_WITH_WARNINGS
        and result.review_path is None
        and critique_failed
    ):
        lines.append(_R4_CRITIQUE_SKIP_NOTE)

    # DRY-RUN passes the proposed text in notes; surface it explicitly.
    for note in result.notes:
        if note == "DRY-RUN: no files written":
            lines.append("(dry run: no files written)")
        elif note.startswith("proposed_state_md:\n"):
            lines.append("--- proposed state.md ---")
            lines.append(note[len("proposed_state_md:\n") :])
            lines.append("--- end ---")

    return "\n".join(lines) + "\n"


def print_sleep_summary(
    result: SleepResult,
    *,
    cfg: Config | None = None,
    console: Console | None = None,
) -> None:
    """Render and print a :class:`SleepResult` to the user."""
    text = render_sleep_summary(result)
    c = console if console is not None else make_console(cfg)
    c.out(text, end="")


# ---------------------------------------------------------------------------
# Topics table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopicsRow:
    name: str
    schema_name: str
    pending: int
    last_chat: str
    last_consolidated: str


def render_topics_table(rows: list[TopicsRow]) -> str:
    """Render the ``remory topics`` table to a plain-text string."""
    if not rows:
        return "No topics yet. Run remory init to set one up.\n"
    headers = ("topic", "schema", "pending", "last chat", "last sleep")
    widths = [
        max(len(headers[0]), max(len(r.name) for r in rows)),
        max(len(headers[1]), max(len(r.schema_name) for r in rows)),
        max(len(headers[2]), max(len(str(r.pending)) for r in rows)),
        max(len(headers[3]), max(len(r.last_chat) for r in rows)),
        max(len(headers[4]), max(len(r.last_consolidated) for r in rows)),
    ]
    fmt = (
        f"{{:<{widths[0]}}}  {{:<{widths[1]}}}  {{:>{widths[2]}}}  "
        f"{{:<{widths[3]}}}  {{:<{widths[4]}}}"
    )
    lines = [fmt.format(*headers)]
    lines.append(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        lines.append(
            fmt.format(
                r.name,
                r.schema_name,
                str(r.pending),
                r.last_chat,
                r.last_consolidated,
            ),
        )
    return "\n".join(lines) + "\n"


def print_topics_table(
    rows: list[TopicsRow],
    *,
    cfg: Config | None = None,
    console: Console | None = None,
) -> None:
    """Render and print the topics table."""
    text = render_topics_table(rows)
    c = console if console is not None else make_console(cfg)
    c.out(text, end="")


# ---------------------------------------------------------------------------
# Prompt helpers (Phase 5 wizard uses; Phase 4 keeps them small)
# ---------------------------------------------------------------------------


def prompt_text(
    prompt: str,
    *,
    console: Console | None = None,
    input_fn: object | None = None,
) -> str:
    """Read a single-line text prompt from stdin, returning the stripped value.

    ``input_fn`` is a test seam (default ``builtins.input``).
    """
    c = console if console is not None else make_console()
    c.out(prompt, end="")
    if input_fn is None:
        line = input()
    else:
        # Test override; cast to a callable returning str.
        from collections.abc import Callable
        from typing import cast

        line = cast("Callable[[], str]", input_fn)()
    return line.strip()


def prompt_line(
    prompt: str,
    *,
    console: Console | None = None,
    input_fn: object | None = None,
) -> str:
    """Read one raw line from stdin without stripping.

    Returns the line as ``input()`` returns it (no trailing newline,
    no ``.strip()``). Wizard validators check newline presence — they
    can't be applied after ``.strip()``. Use this when the validator
    cares about whitespace or embedded newlines (Phase 5 wizard);
    use :func:`prompt_text` for the simple stripped form.

    ``input_fn`` is a test seam (default ``builtins.input``).
    """
    c = console if console is not None else make_console()
    c.out(prompt, end="")
    if input_fn is None:
        return input()
    from collections.abc import Callable
    from typing import cast

    return cast("Callable[[], str]", input_fn)()


def prompt_choice(
    prompt: str,
    *,
    valid: tuple[str, ...],
    console: Console | None = None,
    input_fn: object | None = None,
) -> str:
    """Read a single-line input that must be in ``valid`` (case-insensitive).

    Returns the lower-cased input. Raises :class:`ValueError` on
    invalid input — caller decides whether to re-prompt.
    """
    raw = prompt_text(prompt, console=console, input_fn=input_fn)
    lower = raw.lower()
    if lower not in {v.lower() for v in valid}:
        raise ValueError(f"input {raw!r} not in {valid!r}")
    return lower
