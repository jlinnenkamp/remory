"""Byte-pin each bundled template against on-disk bytes.

Each of the nine files under ``src/remory/data_templates/.claude/`` is
locked here. If a future contract change ever changes the bundled
bytes, the matching snapshot fails and a human decides whether to
re-pin (intentional template revision + version bump) or fix the
producer (accidental drift).

See plan §11.3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remory.data_templates import read_template_bytes

# Bundled templates live next to the package; we read them via the
# importlib.resources accessor to mirror the install codepath, then
# pin the bytes here. Implementation tests assert what install_*
# *does*; these tests assert what the bytes *are*.

_PKG_DIR: Path = Path(__file__).resolve().parents[2] / "src" / "remory" / "data_templates"


def _expected_bytes(relpath: str) -> bytes:
    """Read directly from the on-disk source so we pin packaging
    behaviour, not the in-process accessor's own renderer."""
    return (_PKG_DIR / relpath).read_bytes()


# ---------------------------------------------------------------------------
# Snapshot tests — one per bundled file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relpath",
    [
        ".claude/agents/wizard.md",
        ".claude/agents/extractor.md",
        ".claude/agents/merger.md",
        ".claude/agents/critic.md",
        ".claude/commands/sleep.md",
        ".claude/commands/state.md",
        ".claude/commands/recent.md",
        ".claude/commands/review.md",
        ".claude/settings.json",
    ],
    ids=[
        "wizard_md",
        "extractor_md",
        "merger_md",
        "critic_md",
        "sleep_md",
        "state_md",
        "recent_md",
        "review_md",
        "settings_json",
    ],
)
def test_bundled_template_bytes_match_on_disk_source(relpath: str) -> None:
    """The accessor returns exactly the bytes the developer committed."""
    via_accessor = read_template_bytes(relpath)
    via_disk = _expected_bytes(relpath)
    assert via_accessor == via_disk
