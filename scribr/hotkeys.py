"""
hotkeys.py — Global hotkey listener using AppKit NSEvent global monitors.

Uses NSEvent.addGlobalMonitorForEventsMatchingMask_handler_ which integrates
directly with the Cocoa run loop that rumps is already running.  This is the
most reliable approach on macOS and requires no extra dependencies beyond
PyObjC, which rumps already pulls in.

Two hotkeys are registered:
  1. Right Option key (held) — record while pressed, transcribe on release.
     Detected via NSEventTypeFlagsChanged + keyCode 61.
  2. Selector hotkey (configurable combo, default Ctrl+Shift+Space) — opens
     the model selector dialog.
     Detected via NSEventTypeKeyDown with the required modifier flags set.

Note: NSEvent global monitors do NOT require the app to be focused.  They do
still require Accessibility permission (same as pynput).

Right Option key details:
  - keyCode: 61  (kVK_RightOption)
  - Presence in modifierFlags: NSEventModifierFlagOption (1 << 19)
  - Detected as FlagsChanged; pressed = flag newly set, released = flag cleared.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)

VoidCallback = Callable[[], None]

# macOS key codes
_KEYCODE_RIGHT_OPTION = 61
_KEYCODE_SPACE = 49

# NSEventModifierFlag* values
_MOD_SHIFT = 1 << 17  # NSEventModifierFlagShift
_MOD_CTRL = 1 << 18  # NSEventModifierFlagControl
_MOD_OPT = 1 << 19  # NSEventModifierFlagOption
_MOD_CMD = 1 << 20  # NSEventModifierFlagCommand

# NSEventType masks
_MASK_KEY_DOWN = 1 << 10  # NSEventTypeKeyDown
_MASK_KEY_UP = 1 << 11  # NSEventTypeKeyUp
_MASK_FLAGS_CHANGED = 1 << 12  # NSEventTypeFlagsChanged

# Map config hotkey modifier tokens → flag bits
_MOD_TOKEN_MAP: dict[str, int] = {
    "ctrl": _MOD_CTRL,
    "control": _MOD_CTRL,
    "shift": _MOD_SHIFT,
    "alt": _MOD_OPT,
    "option": _MOD_OPT,
    "cmd": _MOD_CMD,
    "command": _MOD_CMD,
}

# Map config hotkey key-name tokens → macOS key codes
_KEY_TOKEN_MAP: dict[str, int] = {
    "space": _KEYCODE_SPACE,
}


def _parse_hotkey(hotkey_str: str) -> tuple[int, int]:
    """
    Parse a hotkey string like "<ctrl>+<shift>+<space>" into
    (required_modifier_flags, key_code).

    Tokens wrapped in <> are treated as modifiers if they appear in
    _MOD_TOKEN_MAP, otherwise as the trigger key via _KEY_TOKEN_MAP.
    Bare tokens (no <>) are treated as the trigger key by their ord() value.

    Returns (modifier_mask, key_code).  Raises ValueError on bad input.
    """
    parts = [p.strip() for p in hotkey_str.lower().split("+")]
    modifier_mask = 0
    key_code: int | None = None

    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            token = part[1:-1]
            if token in _MOD_TOKEN_MAP:
                modifier_mask |= _MOD_TOKEN_MAP[token]
            elif token in _KEY_TOKEN_MAP:
                if key_code is not None:
                    raise ValueError(f"Multiple trigger keys in hotkey: {hotkey_str!r}")
                key_code = _KEY_TOKEN_MAP[token]
            else:
                raise ValueError(
                    f"Unknown token {token!r} in hotkey {hotkey_str!r}. "
                    f"Known modifiers: {list(_MOD_TOKEN_MAP)}. "
                    f"Known keys: {list(_KEY_TOKEN_MAP)}."
                )
        else:
            # Bare character — use its ASCII code as the key code
            if len(part) != 1:
                raise ValueError(f"Unknown token {part!r} in hotkey {hotkey_str!r}")
            if key_code is not None:
                raise ValueError(f"Multiple trigger keys in hotkey: {hotkey_str!r}")
            key_code = ord(part)

    if key_code is None:
        raise ValueError(f"No trigger key found in hotkey: {hotkey_str!r}")

    return modifier_mask, key_code


class HotkeyListener:
    """
    Manages two global hotkey bindings via AppKit NSEvent global monitors:
      - Record key: right Option, press/release callbacks
      - Selector key: configurable combo, fires on press only
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

        self._lock = threading.Lock()
        self._record_held = False
        self._selector_fired = False  # debounce: only fire once per press

        try:
            self._selector_mod_mask, self._selector_key_code = _parse_hotkey(
                selector_hotkey
            )
        except ValueError:
            log.exception(
                "Failed to parse selector hotkey %r — selector disabled",
                selector_hotkey,
            )
            self._selector_mod_mask = 0
            self._selector_key_code = -1

        # Monitor tokens returned by AppKit (kept so we can remove them on stop)
        self._monitors: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Install NSEvent global monitors. Must be called after the Cocoa
        run loop has started (i.e. after rumps has initialised NSApp)."""
        try:
            from AppKit import NSEvent  # noqa: PLC0415
        except ImportError:
            log.error(
                "AppKit not available — hotkeys disabled. "
                "Ensure PyObjC is installed (it ships with rumps)."
            )
            return

        # Monitor 1: FlagsChanged — right Option press / release
        flags_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            _MASK_FLAGS_CHANGED,
            self._handle_flags_changed,
        )
        if flags_monitor is not None:
            self._monitors.append(flags_monitor)

        # Monitor 2: KeyDown — selector combo
        keydown_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            _MASK_KEY_DOWN,
            self._handle_key_down,
        )
        if keydown_monitor is not None:
            self._monitors.append(keydown_monitor)

        # Monitor 3: KeyUp — selector combo debounce reset
        keyup_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            _MASK_KEY_UP,
            self._handle_key_up,
        )
        if keyup_monitor is not None:
            self._monitors.append(keyup_monitor)

        log.info(
            "Hotkey listener started. Record: right-Option | Selector: %s",
            self._selector_hotkey_str,
        )

    def stop(self) -> None:
        """Remove all NSEvent global monitors."""
        try:
            from AppKit import NSEvent  # noqa: PLC0415
        except ImportError:
            return
        for monitor in self._monitors:
            try:
                NSEvent.removeMonitor_(monitor)
            except Exception:
                log.exception("Error removing NSEvent monitor")
        self._monitors.clear()
        log.info("Hotkey listener stopped")

    def update_selector_hotkey(self, hotkey_str: str) -> None:
        """Update the selector hotkey at runtime."""
        try:
            mod_mask, key_code = _parse_hotkey(hotkey_str)
        except ValueError:
            log.exception(
                "Failed to parse selector hotkey %r — keeping old one", hotkey_str
            )
            return
        with self._lock:
            self._selector_hotkey_str = hotkey_str
            self._selector_mod_mask = mod_mask
            self._selector_key_code = key_code
        log.info("Selector hotkey updated to: %s", hotkey_str)

    # ------------------------------------------------------------------
    # NSEvent handler callbacks (called on the main Cocoa run loop thread)
    # ------------------------------------------------------------------

    def _handle_flags_changed(self, event) -> None:
        """Handle NSEventTypeFlagsChanged — detect right Option press/release."""
        try:
            if event.keyCode() != _KEYCODE_RIGHT_OPTION:
                return
            # Option flag set → key is now pressed; cleared → released
            option_down = bool(event.modifierFlags() & _MOD_OPT)
            with self._lock:
                if option_down and not self._record_held:
                    self._record_held = True
                    log.debug("Record key pressed (right Option)")
                    self._fire(self._on_record_start)
                elif not option_down and self._record_held:
                    self._record_held = False
                    log.debug("Record key released (right Option)")
                    self._fire(self._on_record_stop)
        except Exception:
            log.exception("Error in _handle_flags_changed")

    def _handle_key_down(self, event) -> None:
        """Handle NSEventTypeKeyDown — detect selector combo."""
        try:
            if self._selector_key_code < 0:
                return
            if event.keyCode() != self._selector_key_code:
                return
            # Check that required modifiers are all set (ignore other flags)
            flags = event.modifierFlags()
            with self._lock:
                required = self._selector_mod_mask
                if (flags & required) != required:
                    return
                if self._selector_fired:
                    return
                self._selector_fired = True
            log.debug("Selector hotkey triggered")
            self._fire(self._on_selector)
        except Exception:
            log.exception("Error in _handle_key_down")

    def _handle_key_up(self, event) -> None:
        """Reset selector debounce on key-up of the trigger key."""
        try:
            if event.keyCode() == self._selector_key_code:
                with self._lock:
                    self._selector_fired = False
        except Exception:
            log.exception("Error in _handle_key_up")

    @staticmethod
    def _fire(callback: VoidCallback) -> None:
        try:
            callback()
        except Exception:
            log.exception("Hotkey callback raised")
