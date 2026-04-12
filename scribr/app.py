"""
app.py — rumps menu-bar application.

Coordinates config, transcriber, recorder, hotkeys, and injector.
Manages the menu-bar icon and menu items.

Icon text format:
  "EN"     — ready
  "EN ⟳"  — loading
  "EN ●"  — recording
  "EN …"  — transcribing
  "EN ✕"  — error
"""

from __future__ import annotations

import logging
import subprocess
import threading

import rumps

from . import config as cfg
from .hotkeys import HotkeyListener
from .injector import Injector
from .recorder import Recorder
from .transcriber import Transcriber, TranscriberState

log = logging.getLogger(__name__)

# Suffix appended to the icon base for each transcriber state
_STATE_SUFFIX: dict[TranscriberState, str] = {
    TranscriberState.IDLE: " ⟳",
    TranscriberState.LOADING: " ⟳",
    TranscriberState.READY: "",
    TranscriberState.TRANSCRIBING: " …",
    TranscriberState.ERROR: " ✕",
}

_STATE_LABEL: dict[TranscriberState, str] = {
    TranscriberState.IDLE: "Idle",
    TranscriberState.LOADING: "Loading…",
    TranscriberState.READY: "Ready",
    TranscriberState.TRANSCRIBING: "Transcribing…",
    TranscriberState.ERROR: "Error — check logs",
}

RECORDING_SUFFIX = " ●"


class ScribrApp(rumps.App):
    def __init__(self) -> None:
        self._config = cfg.load()
        active = self._config.active
        initial_icon = (active.icon if active else "??") + " ⟳"

        super().__init__(name="Scribr", title=initial_icon, quit_button=None)

        self._injector = Injector()
        self._recorder = Recorder(on_complete=self._on_audio_ready)
        self._transcriber = Transcriber(
            on_state_change=self._on_transcriber_state,
            on_result=self._on_transcription_result,
            on_error=self._on_transcription_error,
        )
        self._hotkeys = HotkeyListener(
            on_record_start=self._on_record_start,
            on_record_stop=self._on_record_stop,
            on_selector=self._on_selector_hotkey,
            selector_hotkey=self._config.selector_hotkey,
        )

        self._recording = False
        self._current_state = TranscriberState.IDLE
        self._state_lock = threading.Lock()

        self._build_menu()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def run(self) -> None:  # type: ignore[override]
        """Start background services then hand off to rumps event loop."""
        self._transcriber.start()
        self._hotkeys.start()
        self._load_active_model()
        super().run()

    def _load_active_model(self) -> None:
        active = self._config.active
        if active:
            log.info("Loading active model on startup: %s", active.model_id)
            self._transcriber.load(active.model_id)

    # ------------------------------------------------------------------
    # Menu construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        self.menu.clear()

        self._status_item = rumps.MenuItem("Status: Loading…")
        self._status_item.set_callback(None)
        self.menu.add(self._status_item)

        self.menu.add(rumps.separator)

        self._switch_menu = rumps.MenuItem("Switch Model")
        self._populate_switch_menu()
        self.menu.add(self._switch_menu)

        self.menu.add(rumps.separator)

        self.menu.add(rumps.MenuItem("Open Config", callback=self._open_config))
        self.menu.add(rumps.MenuItem("Reload Config", callback=self._reload_config))

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=self._quit))

    def _populate_switch_menu(self) -> None:
        self._switch_menu.clear()
        for model in self._config.enabled_models:
            label = model.label
            if model.key == self._config.active_model:
                label = f"• {label}"
            item = rumps.MenuItem(
                label,
                callback=self._make_switch_callback(model.key),
            )
            self._switch_menu.add(item)

    def _make_switch_callback(self, model_key: str):
        def callback(_):
            self._switch_model(model_key)

        return callback

    # ------------------------------------------------------------------
    # Icon / status helpers (safe to call from any thread)
    # ------------------------------------------------------------------

    def _set_icon(self, icon_text: str) -> None:
        """Update the menu bar title. rumps title assignment is thread-safe."""
        self.title = icon_text

    def _refresh_icon(self) -> None:
        """Recompute and apply the icon from current state."""
        active = self._config.active
        base = active.icon if active else "??"
        with self._state_lock:
            recording = self._recording
            state = self._current_state
        if recording:
            suffix = RECORDING_SUFFIX
        else:
            suffix = _STATE_SUFFIX.get(state, "")
        self._set_icon(base + suffix)

    def _set_status(self, text: str) -> None:
        self._status_item.title = f"Status: {text}"

    # ------------------------------------------------------------------
    # Model switching
    # ------------------------------------------------------------------

    def _switch_model(self, model_key: str) -> None:
        if model_key == self._config.active_model:
            return

        model = self._config.models.get(model_key)
        if not model:
            log.error("Unknown model key: %s", model_key)
            return

        log.info("Switching to model: %s (%s)", model_key, model.model_id)

        # Stop any in-progress recording
        with self._state_lock:
            if self._recording:
                self._recording = False
                self._recorder.stop()

        # Persist selection
        self._config.active_model = model_key
        cfg.save_active_model(model_key)

        # Update UI immediately
        self._refresh_icon()
        self._set_status("Loading…")
        self._populate_switch_menu()

        # Kick off load (unloads previous model first)
        self._transcriber.load(model.model_id)

    # ------------------------------------------------------------------
    # Hotkey callbacks (called from pynput thread)
    # ------------------------------------------------------------------

    def _on_record_start(self) -> None:
        with self._state_lock:
            if self._current_state != TranscriberState.READY:
                return
            if self._recording:
                return
            self._recording = True

        self._refresh_icon()
        self._set_status("Recording…")
        try:
            self._recorder.start()
        except Exception:
            log.exception("Failed to start recorder")
            with self._state_lock:
                self._recording = False
            self._refresh_icon()

    def _on_record_stop(self) -> None:
        with self._state_lock:
            if not self._recording:
                return
            self._recording = False

        self._refresh_icon()
        self._recorder.stop()  # triggers _on_audio_ready

    def _on_selector_hotkey(self) -> None:
        """Show model selector dialog (runs in a background thread to avoid blocking hotkey listener)."""
        threading.Thread(
            target=self._show_model_selector_dialog,
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # Model selector dialog
    # ------------------------------------------------------------------

    def _show_model_selector_dialog(self) -> None:
        models = self._config.enabled_models
        if not models:
            return

        items = ", ".join(f'"{m.label}"' for m in models)
        active = self._config.active
        default_clause = f'default items {{"{active.label}"}}' if active else ""

        script = (
            f"choose from list {{{items}}} "
            f'with title "Scribr \u2014 Switch Model" '
            f'with prompt "Select transcription language:" '
            f"{default_clause}"
        )

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=60,
            )
            chosen_label = result.stdout.strip()
            if not chosen_label or chosen_label.lower() == "false":
                return
            for model in models:
                if model.label == chosen_label:
                    self._switch_model(model.key)
                    return
        except Exception:
            log.exception("Model selector dialog failed")

    # ------------------------------------------------------------------
    # Audio / transcription callbacks (called from background threads)
    # ------------------------------------------------------------------

    def _on_audio_ready(self, audio) -> None:
        """Recorder finished — submit audio to transcriber."""
        log.debug("Audio ready — submitting for transcription")
        self._set_status("Transcribing…")
        self._transcriber.transcribe(audio)

    def _on_transcriber_state(self, state: TranscriberState) -> None:
        """Transcriber state changed — update UI."""
        with self._state_lock:
            self._current_state = state
        self._refresh_icon()
        self._set_status(_STATE_LABEL.get(state, str(state)))

    def _on_transcription_result(self, text: str) -> None:
        """Inference complete — inject text."""
        log.info("Transcription: %r", text)
        if text:
            threading.Thread(
                target=self._injector.type_text,
                args=(text,),
                daemon=True,
                name="injector",
            ).start()

    def _on_transcription_error(self, exc: Exception) -> None:
        log.error("Transcription error: %s", exc)
        self._set_status(f"Error: {exc}")

    # ------------------------------------------------------------------
    # Menu action callbacks
    # ------------------------------------------------------------------

    def _open_config(self, _) -> None:
        subprocess.Popen(["open", str(cfg.CONFIG_PATH)])

    def _reload_config(self, _) -> None:
        log.info("Reloading config from disk")
        self._config = cfg.load()
        self._hotkeys.update_selector_hotkey(self._config.selector_hotkey)
        self._build_menu()
        self._load_active_model()

    def _quit(self, _) -> None:
        log.info("Quitting Scribr")
        self._hotkeys.stop()
        self._transcriber.stop()
        rumps.quit_application()
