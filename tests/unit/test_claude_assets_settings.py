"""Pin the bundled ``settings.json`` shape.

The bytes are byte-pinned in ``test_data_templates_snapshot.py``. The
tests here pin the *semantic* shape: the version key + value, the
SessionEnd command, the PreToolUse matcher. Two-layer pinning so a
re-format that changes whitespace doesn't quietly mask a semantic
drift.

See plan §11.1 + §5.6.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from remory.claude_assets import TEMPLATE_VERSION_KEY
from remory.data_templates import read_template_bytes

_SETTINGS_RELPATH = ".claude/settings.json"
_PKG_DIR: Path = Path(__file__).resolve().parents[2] / "src" / "remory" / "data_templates"


def _load_settings() -> dict[str, object]:
    raw: object = json.loads(read_template_bytes(_SETTINGS_RELPATH).decode("utf-8"))
    assert isinstance(raw, dict), "settings.json top-level must be an object"
    return cast(dict[str, object], raw)


def test_settings_json_bytes_byte_pinned() -> None:
    """Belt-and-suspenders snapshot in this file too — different
    failure mode from test_data_templates_snapshot (which compares
    accessor vs. disk source). Here we compare accessor vs. a frozen
    re-read of the same file, so a refactor that swaps the accessor
    implementation is still pinned against on-disk truth."""
    via_accessor = read_template_bytes(_SETTINGS_RELPATH)
    via_disk = (_PKG_DIR / _SETTINGS_RELPATH).read_bytes()
    assert via_accessor == via_disk


def test_settings_json_pins_template_version_key_and_value() -> None:
    settings = _load_settings()
    assert TEMPLATE_VERSION_KEY in settings
    assert settings[TEMPLATE_VERSION_KEY] == 1


def test_settings_json_session_end_command_uses_remory_hook_session_end() -> None:
    settings = _load_settings()
    hooks = settings["hooks"]
    assert isinstance(hooks, dict)
    hooks_typed = cast(dict[str, object], hooks)
    session_end = hooks_typed["SessionEnd"]
    assert isinstance(session_end, list)
    # Walk into the first matcher's first hook.
    first_matcher = cast(list[object], session_end)[0]
    assert isinstance(first_matcher, dict)
    fm = cast(dict[str, object], first_matcher)
    inner_hooks = fm["hooks"]
    assert isinstance(inner_hooks, list)
    first_hook = cast(list[object], inner_hooks)[0]
    assert isinstance(first_hook, dict)
    fh = cast(dict[str, object], first_hook)
    assert fh["type"] == "command"
    assert fh["command"] == "remory _hook session-end"


def test_settings_json_pre_tool_use_matcher_is_edit_pipe_write() -> None:
    settings = _load_settings()
    hooks = settings["hooks"]
    assert isinstance(hooks, dict)
    hooks_typed = cast(dict[str, object], hooks)
    pre_tool_use = hooks_typed["PreToolUse"]
    assert isinstance(pre_tool_use, list)
    first_matcher = cast(list[object], pre_tool_use)[0]
    assert isinstance(first_matcher, dict)
    fm = cast(dict[str, object], first_matcher)
    assert fm["matcher"] == "Edit|Write"
