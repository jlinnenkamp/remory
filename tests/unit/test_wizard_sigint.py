"""SIGINT-deferral mechanism tests (Phase 5, consolidated plan §11.5).

POSIX-only: ``signal.pthread_sigmask`` is not available on Windows
(ADR 0004). Windows ships best-effort flag-based handling; that path
is exercised in integration but not in this micro-unit suite.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time

import pytest

from remory.wizard._commit import _deferred_sigint

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only mechanism; Windows uses a flag-based handler (ADR 0004)",
)


def _install_default_sigint_handler() -> None:
    """Reinstall the default SIGINT handler.

    pytest installs a custom one for capture; we want the real Python
    KeyboardInterrupt path so the assertions below match runtime
    behaviour.
    """
    signal.signal(signal.SIGINT, signal.default_int_handler)


def test_deferred_sigint_masks_signal_during_block_then_reraises_on_exit() -> None:
    """A SIGINT delivered while masked is queued; unmask raises KeyboardInterrupt."""
    _install_default_sigint_handler()
    pid = os.getpid()

    raised = False

    def fire_signal_after_a_beat() -> None:
        # Give the main thread time to enter the masked block.
        time.sleep(0.05)
        os.kill(pid, signal.SIGINT)

    t = threading.Thread(target=fire_signal_after_a_beat, daemon=True)
    t.start()

    try:
        with _deferred_sigint():
            # Sleep long enough that the SIGINT is delivered while masked.
            # The default handler is installed but masked, so the
            # signal is queued by the kernel.
            time.sleep(0.2)
        # Should not reach this line — unmask raises KeyboardInterrupt.
    except KeyboardInterrupt:
        raised = True
    t.join(timeout=1.0)
    assert raised, "expected KeyboardInterrupt to be raised on unmask"


def test_deferred_sigint_unmask_propagates_queued_signal_as_keyboard_interrupt_to_caller() -> None:
    """Restating the contract: the queued signal lands as KeyboardInterrupt
    in the caller's frame, not as a deferred attribute or return value.
    """
    _install_default_sigint_handler()
    pid = os.getpid()

    def fire() -> None:
        time.sleep(0.05)
        os.kill(pid, signal.SIGINT)

    t = threading.Thread(target=fire, daemon=True)
    t.start()

    with pytest.raises(KeyboardInterrupt), _deferred_sigint():
        time.sleep(0.2)
    t.join(timeout=1.0)


def test_deferred_sigint_double_sigint_within_block_delivered_once_at_unmask() -> None:
    """Two SIGINTs while masked → kernel queues once; one KeyboardInterrupt on unmask.

    POSIX SIGINT is non-realtime; multiple deliveries while masked
    coalesce into a single pending signal. We assert the caller sees
    exactly one KeyboardInterrupt.
    """
    _install_default_sigint_handler()
    pid = os.getpid()

    def fire_twice() -> None:
        time.sleep(0.05)
        os.kill(pid, signal.SIGINT)
        os.kill(pid, signal.SIGINT)

    t = threading.Thread(target=fire_twice, daemon=True)
    t.start()

    interrupt_count = 0
    try:
        with _deferred_sigint():
            time.sleep(0.2)
    except KeyboardInterrupt:
        interrupt_count += 1
    # After leaving the block, no further interrupts should hit us.
    time.sleep(0.05)
    t.join(timeout=1.0)
    assert interrupt_count == 1
