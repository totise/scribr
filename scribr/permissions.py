"""
permissions.py — macOS Accessibility permission check and prompt.

pynput requires Accessibility access to read global key events.
On first run (or if permission has been revoked) we show a native
alert directing the user to System Settings.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)


def check_accessibility() -> bool:
    """
    Return True if Accessibility permission is granted.
    Uses the Quartz API via a small osascript probe.
    """
    try:
        # AXIsProcessTrusted is the canonical check but requires PyObjC.
        # We use a lightweight osascript approach instead.
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke ""',
            ],
            capture_output=True,
            timeout=3,
        )
        return result.returncode == 0
    except Exception:
        # If we can't determine, assume we need permission
        return False


def prompt_accessibility() -> None:
    """Show a macOS alert prompting the user to grant Accessibility access."""
    script = (
        'display alert "Scribr needs Accessibility access" '
        'message "To capture global hotkeys, Scribr must be allowed to control '
        "your computer.\\n\\nGo to: System Settings → Privacy & Security → "
        'Accessibility\\n\\nAdd and enable Scribr (or your terminal / Python)." '
        'buttons {"Open System Settings", "Later"} default button 1'
    )
    open_script = (
        'tell application "System Settings" to activate\n'
        'tell application "System Settings" to reveal anchor "Privacy_Accessibility" '
        'of pane "com.apple.preference.security"'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if "Open System Settings" in result.stdout:
            subprocess.Popen(["osascript", "-e", open_script])
    except Exception:
        log.exception("Failed to show Accessibility prompt")


def ensure_accessibility() -> None:
    """Check for Accessibility permission and prompt if missing."""
    if not check_accessibility():
        log.warning("Accessibility permission not granted — prompting user")
        prompt_accessibility()
