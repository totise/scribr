"""
hotkeys.py — Global hotkey listener using pynput.

Two hotkeys are registered:
  1. Right Option key (held) — record while pressed, transcribe on release.
  2. Selector hotkey (configurable) — open the model selector.

pynput on macOS requires Accessibility permission. See permissions.py for
the first-run prompt.

Note on the right Option key:
  pynput surfaces it as pynput.keyboard.Key.alt_r on macOS.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from pynput import keyboard

log = logging.getLogger(__name__)

# The right Option key on macOS
RECORD_KEY = keyboard.Key.alt_r

VoidCallback = Callable[[], None]


def _parse_hotkey(hotkey_str: str) -> frozenset:
    """
    Convert a hotkey string like "<ctrl>+<shift>+<space>" into a frozenset
    of pynput key objects, matching the format used by pynput.HotKey.parse().
    """
    return frozenset(keyboard.HotKey.parse(hotkey_str))


class HotkeyListener:
    """
    Manages two global hotkey bindings:
      - Record key: right Option, press/release callbacks
      - Selector key: configurable combo, press callback only
    """

    def __init__(
        self,
        on_record_start: VoidCallback | None = None,
        on_record_stop: VoidCallback | None = None,
        on_selector: VoidCallback | None = None,
        selector_hotkey: str = "<ctrl>+<shift>+<space>",
    ) -> None:
        self._on_record_start = on_record_start or (lambda: None)
        self._on_record_stop = on_record_stop or (lambda: None)
        self._on_selector = on_selector or (lambda: None)
        self._selector_hotkey_str = selector_hotkey

        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()

        # Track which keys are currently pressed (for combo detection)
        self._pressed: set = set()
        # Parse the selector combo
        self._selector_keys = _parse_hotkey(selector_hotkey)
        # Whether the record key is currently held
        self._record_held = False
        # Whether the selector hotkey has been fired (debounce)
        self._selector_fired = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start listening for global hotkeys. Non-blocking."""
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()
        log.info(
            "Hotkey listener started. Record: right-Option | Selector: %s",
            self._selector_hotkey_str,
        )

    def stop(self) -> None:
        """Stop the global hotkey listener."""
        if self._listener:
            self._listener.stop()
            self._listener = None

    def update_selector_hotkey(self, hotkey_str: str) -> None:
        """Update the selector hotkey without restarting the listener."""
        with self._lock:
            self._selector_hotkey_str = hotkey_str
            self._selector_keys = _parse_hotkey(hotkey_str)
        log.info("Selector hotkey updated to: %s", hotkey_str)

    # ------------------------------------------------------------------
    # pynput callbacks (called from listener thread)
    # ------------------------------------------------------------------

    def _canonical(self, key) -> object:
        """Return the canonical form of a key for reliable comparison."""
        if self._listener:
            return self._listener.canonical(key)
        return key

    def _on_press(self, key) -> None:
        canonical = self._canonical(key)
        with self._lock:
            self._pressed.add(canonical)

            # --- Record key ---
            if canonical == RECORD_KEY and not self._record_held:
                self._record_held = True
                log.debug("Record key pressed")
                try:
                    self._on_record_start()
                except Exception:
                    log.exception("on_record_start raised")

            # --- Selector combo ---
            if (
                not self._selector_fired
                and self._selector_keys
                and self._selector_keys.issubset(self._pressed)
            ):
                self._selector_fired = True
                log.debug("Selector hotkey triggered")
                try:
                    self._on_selector()
                except Exception:
                    log.exception("on_selector raised")

    def _on_release(self, key) -> None:
        canonical = self._canonical(key)
        with self._lock:
            self._pressed.discard(canonical)

            # Reset selector debounce when all selector keys are released
            if self._selector_fired:
                if not self._selector_keys.issubset(self._pressed):
                    self._selector_fired = False

            # --- Record key released ---
            if canonical == RECORD_KEY and self._record_held:
                self._record_held = False
                log.debug("Record key released")
                try:
                    self._on_record_stop()
                except Exception:
                    log.exception("on_record_stop raised")
