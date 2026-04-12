"""
injector.py — Type transcribed text into the currently focused window.

Uses pynput's keyboard controller which works across all macOS apps.
Handles unicode characters correctly by using the type() method.

A small delay is inserted before typing to allow the user to move focus
back to the target window after releasing the record key.
"""

from __future__ import annotations

import logging
import time

from pynput.keyboard import Controller

log = logging.getLogger(__name__)

# Seconds to wait after record key release before typing.
# Gives the OS time to restore focus to the previous window.
PRE_TYPE_DELAY = 0.15


class Injector:
    """Types text into whatever window currently has keyboard focus."""

    def __init__(self, pre_type_delay: float = PRE_TYPE_DELAY) -> None:
        self._keyboard = Controller()
        self._pre_type_delay = pre_type_delay

    def type_text(self, text: str) -> None:
        """
        Type text into the active window.
        Adds a trailing space for convenience (easy to continue typing).
        """
        if not text:
            return

        if self._pre_type_delay > 0:
            time.sleep(self._pre_type_delay)

        output = text + " "
        log.debug("Injecting %d characters", len(output))
        try:
            self._keyboard.type(output)
        except Exception:
            log.exception("Failed to inject text")
