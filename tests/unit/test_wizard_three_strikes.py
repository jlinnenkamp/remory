"""Three-strikes counter tests (Phase 5, consolidated plan §11.2).

Tests pin: per-question counter resets on the next prompt; bail
behaviour after 3 consecutive invalid attempts on the *same* prompt;
CLI mapping to the locked message + exit 2.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from remory.cli.errors import format_error
from remory.wizard import WizardThreeStrikesError
from remory.wizard._steps import _prompt_with_validator
from remory.wizard._validators import (
    validate_choice_with_skip,
    validate_name,
)
from tests.fakes.scripted_input import ScriptedInput


def _quiet_console() -> Console:
    """Console writing into a /dev/null buffer for noise isolation."""
    return Console(file=io.StringIO(), color_system=None, no_color=True)


def test_prompt_with_validator_reraises_after_three_consecutive_invalid_attempts_on_same_prompt() -> (  # noqa: E501  # pinned name from consolidated plan §11.2 — encodes the contract
    None
):
    """Three rejected lines in a row → WizardThreeStrikesError."""
    fake = ScriptedInput(["", "", ""])
    with pytest.raises(WizardThreeStrikesError):
        _prompt_with_validator(
            "What should I call you?\n> ",
            validate_name,
            console=_quiet_console(),
            input_fn=fake,
        )


def test_prompt_with_validator_resets_attempt_counter_after_valid_input_within_run() -> None:
    """Counter resets per CALL: a separate question starts at 0 attempts.

    First call: reject twice, then accept on the 3rd line.
    Second call (separate invocation): two rejects + one accept must
    succeed, proving the counter does not leak across calls.
    """
    fake = ScriptedInput(["", "", "Sam"])
    result = _prompt_with_validator(
        "name?\n> ",
        validate_name,
        console=_quiet_console(),
        input_fn=fake,
    )
    assert result == "Sam"

    fake2 = ScriptedInput(["", "", "1"])
    result2 = _prompt_with_validator(
        "choice?\n> ",
        validate_choice_with_skip,
        console=_quiet_console(),
        input_fn=fake2,
    )
    assert result2 == "1"


def test_format_error_maps_wizard_three_strikes_to_locked_message_exit_2(
    tmp_path: Path,
) -> None:
    msg, code = format_error(WizardThreeStrikesError("..."), data_dir=tmp_path)
    assert code == 2
    assert "Three tries — let's stop here. Run remory init again when you're ready." in msg
