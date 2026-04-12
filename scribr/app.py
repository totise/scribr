"""
app.py — rumps menu-bar application.

Coordinates config, transcriber, pipeline, hotkeys, and injector.
Manages the menu-bar icon and menu items.

Icon text format:
  "EN"     — ready
  "EN ⟳"  — loading model
  "EN ●"  — recording (batch) or recording before first chunk (chunked)
  "EN ●…" — recording + parallel transcription in progress (chunked)
  "EN …"  — transcribing final chunk / full batch
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
from .pipeline import PipelineState, _BasePipeline, make_pipeline
from .transcriber import Transcriber, TranscriberState

log = logging.getLogger(__name__)

# Menu-bar icon suffixes per pipeline state
_PIPELINE_SUFFIX: dict[PipelineState, str] = {
    PipelineState.IDLE: "",
    PipelineState.RECORDING: " ●",
    PipelineState.RECORDING_TRANSCRIBING: " ●…",
    PipelineState.TRANSCRIBING: " …",
    PipelineState.DONE: "",
}

# Menu-bar icon suffixes per transcriber state (used when no pipeline is active)
_TRANSCRIBER_SUFFIX: dict[TranscriberState, str] = {
    TranscriberState.IDLE: " ⟳",
    TranscriberState.LOADING: " ⟳",
    TranscriberState.READY: "",
    TranscriberState.TRANSCRIBING: " …",
    TranscriberState.ERROR: " ✕",
}

_TRANSCRIBER_LABEL: dict[TranscriberState, str] = {
    TranscriberState.IDLE: "Idle",
    TranscriberState.LOADING: "Loading…",
    TranscriberState.READY: "Ready",
    TranscriberState.TRANSCRIBING: "Transcribing…",
    TranscriberState.ERROR: "Error — check logs",
}


class ScribrApp(rumps.App):
    def __init__(self) -> None:
        self._config = cfg.load()
        active = self._config.active
        initial_icon = (active.icon if active else "??") + " ⟳"

        super().__init__(name="Scribr", title=initial_icon, quit_button=None)

        self._injector = Injector()
        self._transcriber = Transcriber(
            on_state_change=self._on_transcriber_state,
            on_error=self._on_transcription_error,
        )
        self._pipeline: _BasePipeline | None = None
        self._pipeline_lock = threading.Lock()

        self._hotkeys = HotkeyListener(
            on_record_start=self._on_record_start,
            on_record_stop=self._on_record_stop,
            on_selector=self._on_selector_hotkey,
            selector_hotkey=self._config.selector_hotkey,
        )

        self._transcriber_state = TranscriberState.IDLE
        self._pipeline_state = PipelineState.IDLE
        self._state_lock = threading.Lock()

        self._build_menu()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def run(self) -> None:  # type: ignore[override]
        self._transcriber.start()
        self._hotkeys.start()
        self._load_active_model()
        super().run()

    def _load_active_model(self) -> None:
        active = self._config.active
        if not active:
            return
        log.info(
            "Loading active model: %s (strategy=%s)", active.model_id, active.strategy
        )
        self._transcriber.load(active.model_id)

    def _rebuild_pipeline(self) -> None:
        """Instantiate the correct pipeline for the currently active model."""
        active = self._config.active
        if not active:
            return
        with self._pipeline_lock:
            self._pipeline = make_pipeline(
                strategy=active.strategy,
                transcriber=self._transcriber,
                chunk_seconds=active.chunk_seconds,
                overlap_seconds=active.overlap_seconds,
            )
            self._pipeline.on_result = self._on_pipeline_result
            self._pipeline.on_state_change = self._on_pipeline_state
        log.info("Pipeline ready: %s", active.strategy)

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
    # Icon / status helpers
    # ------------------------------------------------------------------

    def _icon_base(self) -> str:
        active = self._config.active
        return active.icon if active else "??"

    def _refresh_icon(self) -> None:
        with self._state_lock:
            ps = self._pipeline_state
            ts = self._transcriber_state

        base = self._icon_base()

        # Pipeline state takes precedence when active
        if ps not in (PipelineState.IDLE, PipelineState.DONE):
            suffix = _PIPELINE_SUFFIX.get(ps, "")
        else:
            suffix = _TRANSCRIBER_SUFFIX.get(ts, "")

        self.title = base + suffix

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

        log.info(
            "Switching to model: %s (%s, strategy=%s)",
            model_key,
            model.model_id,
            model.strategy,
        )

        # Stop any active recording
        with self._pipeline_lock:
            pipeline = self._pipeline
        if pipeline and pipeline.is_active:
            pipeline.stop_recording()

        # Persist selection
        self._config.active_model = model_key
        cfg.save_active_model(model_key)

        # Reset pipeline while new model loads
        with self._pipeline_lock:
            self._pipeline = None
        with self._state_lock:
            self._pipeline_state = PipelineState.IDLE

        self._refresh_icon()
        self._set_status("Loading…")
        self._populate_switch_menu()

        # Request new model load; pipeline is rebuilt in _on_transcriber_state → READY
        self._transcriber.load(model.model_id)

    # ------------------------------------------------------------------
    # Hotkey callbacks (pynput thread)
    # ------------------------------------------------------------------

    def _on_record_start(self) -> None:
        with self._pipeline_lock:
            pipeline = self._pipeline
        if pipeline is None:
            log.debug("Record start ignored — no pipeline (model loading?)")
            return
        pipeline.start_recording()

    def _on_record_stop(self) -> None:
        with self._pipeline_lock:
            pipeline = self._pipeline
        if pipeline is None:
            return
        pipeline.stop_recording()

    def _on_selector_hotkey(self) -> None:
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
    # Pipeline callbacks (background threads)
    # ------------------------------------------------------------------

    def _on_pipeline_result(self, text: str) -> None:
        """Text is ready — inject into active window."""
        log.info("Pipeline result: %r", text)
        threading.Thread(
            target=self._injector.type_text,
            args=(text,),
            daemon=True,
            name="injector",
        ).start()

    def _on_pipeline_state(self, state: PipelineState) -> None:
        with self._state_lock:
            self._pipeline_state = state
        self._refresh_icon()

        labels = {
            PipelineState.IDLE: _TRANSCRIBER_LABEL.get(
                self._transcriber_state, "Ready"
            ),
            PipelineState.RECORDING: "Recording…",
            PipelineState.RECORDING_TRANSCRIBING: "Recording + transcribing…",
            PipelineState.TRANSCRIBING: "Transcribing…",
            PipelineState.DONE: "Done",
        }
        self._set_status(labels.get(state, str(state)))

    # ------------------------------------------------------------------
    # Transcriber callbacks (background thread)
    # ------------------------------------------------------------------

    def _on_transcriber_state(self, state: TranscriberState) -> None:
        with self._state_lock:
            self._transcriber_state = state

        # When model becomes READY, build/rebuild the pipeline
        if state == TranscriberState.READY:
            self._rebuild_pipeline()

        self._refresh_icon()
        self._set_status(_TRANSCRIBER_LABEL.get(state, str(state)))

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
