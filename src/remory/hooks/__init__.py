"""Claude Code hooks Typer subapp — ``remory _hook ...``.

The bundled ``settings.json`` invokes these commands per plan §5.6:

- ``remory _hook session-end`` — capture transcript as raw entry.
- ``remory _hook pretool``     — refuse Edit/Write to state.md.

Both subcommands read claude's JSON payload from stdin, dispatch to the
pure :func:`remory.hooks.session_end.run` /
:func:`remory.hooks.pre_tool_use.decide` helpers, and exit per the hook
protocol. The subapp is registered with ``hidden=True`` on the parent
CLI so it does not appear in ``remory --help``.
"""

from __future__ import annotations

import typer

from remory.hooks import pre_tool_use, session_end

__all__ = ["app"]


app = typer.Typer(
    name="_hook",
    help="Internal: claude hook entry points (invoked by settings.json).",
    no_args_is_help=True,
    add_completion=False,
)


@app.command("session-end")
def cmd_session_end() -> None:
    """SessionEnd hook entry point.

    Exits 0 ALWAYS — hooks must not block claude. Errors are logged but
    swallowed (see :func:`remory.hooks.session_end.run`).
    """
    code = session_end.main()
    raise typer.Exit(code=code)


@app.command("pretool")
def cmd_pretool() -> None:
    """PreToolUse hook entry point.

    Exits 0 on allow, 2 on deny (block) per claude's hook contract.
    """
    code = pre_tool_use.main()
    raise typer.Exit(code=code)
