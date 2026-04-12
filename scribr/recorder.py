"""
recorder.py — Microphone capture using sounddevice.

Records audio while a start() / stop() pair is called (driven by hotkey press/release).
Delivers the completed float32 mono 16 kHz numpy array via an on_complete callback.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000  # Hz — matches Parakeet model input requirement
CHANNELS = 1
DTYPE = "float32"
BLOCKSIZE = 1024  # frames per callback (~64 ms at 16 kHz)

AudioCallback = Callable[[np.ndarray], None]


class Recorder:
    """
    Press-hold-release audio recorder.

    Usage:
        rec = Recorder(on_complete=handle_audio)
        rec.start()   # called on hotkey press
        rec.stop()    # called on hotkey release — triggers on_complete
    """

    def __init__(
        self,
        on_complete: AudioCallback | None = None,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self._on_complete = on_complete or (lambda _: None)
        self._sample_rate = sample_rate
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._recording = False

    # ------------------------------------------------------------------
    # Public API (called from hotkey thread)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin capturing audio. Safe to call from any thread."""
        with self._lock:
            if self._recording:
                log.warning("Recorder.start() called while already recording — ignored")
                return
            self._chunks = []
            self._recording = True
            try:
                self._stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    blocksize=BLOCKSIZE,
                    callback=self._audio_callback,
                )
                self._stream.start()
                log.debug("Recording started")
            except Exception as exc:
                self._recording = False
                self._stream = None
                log.exception("Failed to open audio stream: %s", exc)
                raise

    def stop(self) -> None:
        """Stop recording and fire on_complete with the captured audio."""
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            stream = self._stream
            chunks = list(self._chunks)
            self._stream = None
            self._chunks = []

        # Close stream outside the lock to avoid potential deadlock with the
        # audio callback trying to acquire the lock.
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("Error closing audio stream")

        if not chunks:
            log.debug("Recording stopped with no audio captured")
            return

        audio = np.concatenate(chunks, axis=0).squeeze()
        duration = len(audio) / self._sample_rate
        log.debug("Recording stopped: %.2f seconds captured", duration)

        try:
            self._on_complete(audio)
        except Exception:
            log.exception("on_complete callback raised")

    @property
    def is_recording(self) -> bool:
        return self._recording

    # ------------------------------------------------------------------
    # sounddevice callback (called from audio thread)
    # ------------------------------------------------------------------

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,  # noqa: ARG002
        time,  # noqa: ARG002
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            log.warning("Audio stream status: %s", status)
        with self._lock:
            if self._recording:
                self._chunks.append(indata.copy())
