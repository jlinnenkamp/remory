"""Tests for :mod:`remory.logging_setup`."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import pytest

from remory.logging_setup import configure


def test_configure_warning_keeps_console_handler_at_warning(tmp_path: Path) -> None:
    log_file = tmp_path / "remory.log"
    configure(verbosity="warning", log_file=log_file)
    root = logging.getLogger("remory")
    levels = sorted({h.level for h in root.handlers if isinstance(h, logging.StreamHandler)})
    # File handler is also a StreamHandler subclass, so we just assert
    # that at least one console handler stays at WARNING.
    assert logging.WARNING in levels


def test_configure_verbose_lifts_console_to_info(tmp_path: Path) -> None:
    log_file = tmp_path / "remory.log"
    configure(verbosity="info", log_file=log_file)
    root = logging.getLogger("remory")
    console_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert any(h.level == logging.INFO for h in console_handlers)


def test_configure_debug_lifts_console_to_debug(tmp_path: Path) -> None:
    log_file = tmp_path / "remory.log"
    configure(verbosity="debug", log_file=log_file)
    root = logging.getLogger("remory")
    console_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert any(h.level == logging.DEBUG for h in console_handlers)


def test_configure_returns_log_file_path_when_writable(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "remory.log"
    result = configure(verbosity="warning", log_file=log_file)
    assert result == log_file
    assert log_file.parent.is_dir()


def test_configure_idempotent_does_not_double_attach_handlers(tmp_path: Path) -> None:
    log_file = tmp_path / "remory.log"
    configure(verbosity="warning", log_file=log_file)
    first = list(logging.getLogger("remory").handlers)
    configure(verbosity="warning", log_file=log_file)
    second = list(logging.getLogger("remory").handlers)
    assert len(first) == len(second)


def test_configure_writes_debug_level_messages_to_file(tmp_path: Path) -> None:
    log_file = tmp_path / "remory.log"
    configure(verbosity="warning", log_file=log_file)
    logging.getLogger("remory.test").debug("hello-debug-line")
    # Force flush.
    for h in logging.getLogger("remory").handlers:
        h.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "hello-debug-line" in text


# Avoid an "unused import" warning for the import shape pyright cares about.
_ = pytest
