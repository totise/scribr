"""
recorder.py — Microphone capture using sounddevice.

Two modes:
  Batch (default):
    Records while start()/stop() are held. Fires on_complete(audio) on stop.

  Chunked:
    Same press/hold/release, but also fires on_chunk(audio) periodically while
    recording. Each chunk is chunk_seconds long and includes an overlap_seconds
    prefix taken from the tail of the previous step, so the pipeline can stitch
    boundaries correctly.

    on_complete fires with the remaining tail audio (from the last chunk
    boundary to the release point) so the pipeline can flush the final words.
    If the recording was shorter than one full chunk, on_complete fires with all
    the audio (same behaviour as batch mode).

Callback signatures:
    on_chunk(audio: np.ndarray)     — periodic chunk (chunked mode only)
    on_complete(audio: np.ndarray)  — full audio (batch) or tail (chunked)
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
BLOCKSIZE = 1024  # frames per sounddevice callback (~64 ms at 16 kHz)
MIN_CHUNK_SECONDS = 0.8  # chunks shorter than this are skipped

AudioCallback = Callable[[np.ndarray], None]


class Recorder:
    """
    Press-hold-release audio recorder with optional chunked delivery.

    Usage (batch):
        rec = Recorder(on_complete=handle_audio)
        rec.start()
        rec.stop()   # → on_complete(full_audio)

    Usage (chunked):
        rec = Recorder(
            on_chunk=handle_chunk,     # fires every step_seconds while held
            on_complete=handle_tail,   # fires with remaining tail on release
            chunk_seconds=2.5,
            overlap_seconds=0.5,
        )
        rec.start()
        rec.stop()
    """

    def __init__(
        self,
        on_complete: AudioCallback | None = None,
        on_chunk: AudioCallback | None = None,
        sample_rate: int = SAMPLE_RATE,
        chunk_seconds: float = 2.5,
        overlap_seconds: float = 0.5,
    ) -> None:
        self._on_complete = on_complete or (lambda _: None)
        self._on_chunk = on_chunk or (lambda _: None)
        self._sample_rate = sample_rate
        self._chunk_frames = int(chunk_seconds * sample_rate)
        self._overlap_frames = int(overlap_seconds * sample_rate)
        self._step_frames = self._chunk_frames - self._overlap_frames
        self._min_frames = int(MIN_CHUNK_SECONDS * sample_rate)
        self._chunked_mode = on_chunk is not None

        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._buffer: list[np.ndarray] = []  # all captured audio so far
        self._buffer_len = 0  # total frames in buffer
        self._next_chunk_at = 0  # frame index for next chunk boundary
        self._recording = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin capturing audio. Safe to call from any thread."""
        with self._lock:
            if self._recording:
                log.warning("Recorder.start() called while already recording — ignored")
                return
            self._buffer = []
            self._buffer_len = 0
            self._next_chunk_at = self._chunk_frames
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
                log.debug(
                    "Recording started (mode=%s)",
                    "chunked" if self._chunked_mode else "batch",
                )
            except Exception as exc:
                self._recording = False
                self._stream = None
                log.exception("Failed to open audio stream: %s", exc)
                raise

    def stop(self) -> None:
        """Stop recording and deliver audio via callbacks."""
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            stream = self._stream
            self._stream = None
            # Snapshot the buffer
            full_audio = (
                np.concatenate(self._buffer, axis=0).squeeze()
                if self._buffer
                else np.array([], dtype=np.float32)
            )
            last_chunk_at = (
                self._next_chunk_at - self._step_frames
            )  # last emitted chunk boundary
            self._buffer = []
            self._buffer_len = 0

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("Error closing audio stream")

        if len(full_audio) == 0:
            log.debug("Recording stopped with no audio captured")
            return

        duration = len(full_audio) / self._sample_rate
        log.debug("Recording stopped: %.2f seconds total", duration)

        if not self._chunked_mode:
            # Batch mode: deliver everything
            self._fire(self._on_complete, full_audio)
            return

        # Chunked mode: deliver the tail from the last chunk boundary
        # (with overlap prepended so the pipeline can stitch it)
        tail_start = max(0, last_chunk_at - self._overlap_frames)
        tail = full_audio[tail_start:]
        if len(tail) >= self._min_frames:
            log.debug(
                "Firing on_complete with tail: %.2fs", len(tail) / self._sample_rate
            )
            self._fire(self._on_complete, tail)
        else:
            log.debug(
                "Tail too short (%.2fs) — skipping on_complete",
                len(tail) / self._sample_rate,
            )

    @property
    def is_recording(self) -> bool:
        return self._recording

    # ------------------------------------------------------------------
    # sounddevice callback (audio thread)
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

        chunks_to_fire: list[np.ndarray] = []

        with self._lock:
            if not self._recording:
                return
            self._buffer.append(indata.copy())
            self._buffer_len += len(indata)

            if self._chunked_mode:
                # Check if we've accumulated enough for one or more chunks
                while self._buffer_len >= self._next_chunk_at:
                    full = np.concatenate(self._buffer, axis=0).squeeze()
                    # Chunk: from (next_chunk_at - chunk_frames) to next_chunk_at
                    start = max(0, self._next_chunk_at - self._chunk_frames)
                    chunk = full[start : self._next_chunk_at].copy()
                    chunks_to_fire.append(chunk)
                    self._next_chunk_at += self._step_frames

        # Fire callbacks outside the lock
        for chunk in chunks_to_fire:
            if len(chunk) >= self._min_frames:
                log.debug("Firing on_chunk: %.2fs", len(chunk) / self._sample_rate)
                self._fire(self._on_chunk, chunk)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fire(cb: AudioCallback, audio: np.ndarray) -> None:
        try:
            cb(audio)
        except Exception:
            log.exception("Audio callback raised")
