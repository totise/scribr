"""
transcriber.py — NeMo model loader and inference worker.

Design:
- One model is held in memory at a time.
- A background thread handles both loading and inference so the UI never blocks.
- Callers communicate via callbacks (on_state_change, on_result, on_error,
  on_partial_result).

Two transcription modes:
  transcribe(audio)               — batch: fires on_result(text) when done
  transcribe_chunk(audio, chunk_id) — chunked: fires on_partial_result(text, chunk_id)

States:
  idle         — no model loaded
  loading      — model being loaded in background
  ready        — model loaded, waiting for audio
  transcribing — inference in progress
  error        — last operation failed
"""

from __future__ import annotations

import logging
import os
import queue
import tempfile
import threading
import wave
from enum import Enum, auto
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

# Type aliases
StateCallback = Callable[["TranscriberState"], None]
ResultCallback = Callable[[str], None]
PartialResultCallback = Callable[[str, int], None]  # (text, chunk_id)
ErrorCallback = Callable[[Exception], None]

SAMPLE_RATE = 16_000  # Hz — required by all Parakeet models


class TranscriberState(Enum):
    IDLE = auto()
    LOADING = auto()
    READY = auto()
    TRANSCRIBING = auto()
    ERROR = auto()


# ------------------------------------------------------------------
# Internal command types
# ------------------------------------------------------------------


class _Command:
    pass


class _LoadCmd(_Command):
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id


class _TranscribeCmd(_Command):
    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio


class _TranscribeChunkCmd(_Command):
    def __init__(self, audio: np.ndarray, chunk_id: int) -> None:
        self.audio = audio
        self.chunk_id = chunk_id


class _UnloadCmd(_Command):
    pass


class _StopCmd(_Command):
    pass


# ------------------------------------------------------------------
# Transcriber
# ------------------------------------------------------------------


class Transcriber:
    """
    Single-model transcription engine.

    Usage (batch):
        t = Transcriber(on_result=handle_text, ...)
        t.start()
        t.load("nvidia/parakeet-tdt-0.6b-v2")
        t.transcribe(audio)          # → on_result(text)

    Usage (chunked):
        t = Transcriber(on_partial_result=handle_partial, ...)
        t.start()
        t.load("nvidia/parakeet-rnnt-110m-da-dk")
        t.transcribe_chunk(audio, chunk_id=0)   # → on_partial_result(text, 0)
        t.transcribe_chunk(tail,  chunk_id=1)   # → on_partial_result(text, 1)
    """

    def __init__(
        self,
        on_state_change: StateCallback | None = None,
        on_result: ResultCallback | None = None,
        on_partial_result: PartialResultCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self._on_state_change = on_state_change or (lambda _: None)
        self._on_result = on_result or (lambda _: None)
        self._on_partial_result = on_partial_result or (lambda t, i: None)
        self._on_error = on_error or (lambda _: None)

        self._queue: queue.Queue[_Command] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._state = TranscriberState.IDLE
        self._model = None
        self._current_model_id: str | None = None

    # ------------------------------------------------------------------
    # Public API (thread-safe, non-blocking)
    # ------------------------------------------------------------------

    @property
    def state(self) -> TranscriberState:
        return self._state

    @property
    def current_model_id(self) -> str | None:
        return self._current_model_id

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="transcriber"
        )
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(_StopCmd())
        if self._thread:
            self._thread.join(timeout=5)

    def load(self, model_id: str) -> None:
        """Request loading of the given NeMo model ID. Non-blocking."""
        self._queue.put(_LoadCmd(model_id))

    def unload(self) -> None:
        """Request unloading the current model to free RAM. Non-blocking."""
        self._queue.put(_UnloadCmd())

    def transcribe(self, audio: np.ndarray) -> None:
        """
        Batch transcription. Fires on_result(text) when complete.
        audio: float32 numpy array, mono, 16 kHz.
        """
        if self._state != TranscriberState.READY:
            log.warning("transcribe() called but state is %s — ignoring", self._state)
            return
        self._queue.put(_TranscribeCmd(audio))

    def transcribe_chunk(self, audio: np.ndarray, chunk_id: int) -> None:
        """
        Chunked transcription. Fires on_partial_result(text, chunk_id) when complete.
        Safe to call while a previous chunk is still being processed — commands
        are queued and executed in order.
        audio: float32 numpy array, mono, 16 kHz.
        """
        if self._state not in (TranscriberState.READY, TranscriberState.TRANSCRIBING):
            log.warning(
                "transcribe_chunk() called but state is %s — ignoring", self._state
            )
            return
        self._queue.put(_TranscribeChunkCmd(audio, chunk_id))

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _set_state(self, state: TranscriberState) -> None:
        self._state = state
        try:
            self._on_state_change(state)
        except Exception:
            log.exception("on_state_change callback raised")

    def _worker(self) -> None:
        while True:
            cmd = self._queue.get()

            if isinstance(cmd, _StopCmd):
                self._do_unload()
                self._set_state(TranscriberState.IDLE)
                break
            elif isinstance(cmd, _LoadCmd):
                self._do_load(cmd.model_id)
            elif isinstance(cmd, _UnloadCmd):
                self._do_unload()
            elif isinstance(cmd, _TranscribeCmd):
                self._do_transcribe(cmd.audio)
            elif isinstance(cmd, _TranscribeChunkCmd):
                self._do_transcribe_chunk(cmd.audio, cmd.chunk_id)
            elif isinstance(cmd, _MarkDoneCmd):
                if self._state == TranscriberState.TRANSCRIBING:
                    self._set_state(TranscriberState.READY)
                    log.debug("All chunks processed — state → READY")

    def _do_load(self, model_id: str) -> None:
        if self._model is not None:
            self._do_unload()

        self._set_state(TranscriberState.LOADING)
        log.info("Loading model: %s", model_id)
        try:
            import nemo.collections.asr as nemo_asr  # noqa: PLC0415

            model = nemo_asr.models.ASRModel.from_pretrained(model_id)
            model.eval()
            self._model = model
            self._current_model_id = model_id
            self._set_state(TranscriberState.READY)
            log.info("Model ready: %s", model_id)
        except Exception as exc:
            log.exception("Failed to load model %s", model_id)
            self._set_state(TranscriberState.ERROR)
            self._fire_error(exc)

    def _do_unload(self) -> None:
        if self._model is None:
            return
        log.info("Unloading model: %s", self._current_model_id)
        try:
            del self._model
            self._model = None
            self._current_model_id = None
            try:
                import torch  # noqa: PLC0415

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
        except Exception:
            log.exception("Error during model unload")
        self._set_state(TranscriberState.IDLE)

    def _do_transcribe(self, audio: np.ndarray) -> None:
        if self._model is None:
            log.error("transcribe called with no model loaded")
            return
        self._set_state(TranscriberState.TRANSCRIBING)
        log.info("Transcribing %.1fs of audio (batch)", len(audio) / SAMPLE_RATE)
        try:
            text = self._run_inference(audio)
            self._set_state(TranscriberState.READY)
            log.info("Batch result: %r", text)
            try:
                self._on_result(text)
            except Exception:
                log.exception("on_result callback raised")
        except Exception as exc:
            log.exception("Batch transcription failed")
            self._set_state(TranscriberState.ERROR)
            self._fire_error(exc)

    def _do_transcribe_chunk(self, audio: np.ndarray, chunk_id: int) -> None:
        if self._model is None:
            log.error("transcribe_chunk called with no model loaded")
            return
        # Stay in TRANSCRIBING across consecutive chunks; only return to READY
        # after the last chunk (signalled externally by the pipeline).
        prev_state = self._state
        if prev_state == TranscriberState.READY:
            self._set_state(TranscriberState.TRANSCRIBING)
        log.info("Transcribing %.1fs chunk #%d", len(audio) / SAMPLE_RATE, chunk_id)
        try:
            text = self._run_inference(audio)
            log.info("Chunk #%d result: %r", chunk_id, text)
            try:
                self._on_partial_result(text, chunk_id)
            except Exception:
                log.exception("on_partial_result callback raised")
        except Exception as exc:
            log.exception("Chunk #%d transcription failed", chunk_id)
            self._set_state(TranscriberState.ERROR)
            self._fire_error(exc)

    def mark_chunks_done(self) -> None:
        """
        Called by the pipeline after the final chunk has been queued.
        Enqueues a sentinel that transitions state back to READY once the
        worker processes it (i.e. after all queued chunks complete).
        """
        self._queue.put(_MarkDoneCmd())

    def _run_inference(self, audio: np.ndarray) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            _write_wav(tmp_path, audio, SAMPLE_RATE)
            output = self._model.transcribe([tmp_path])
            result = output[0]
            if hasattr(result, "text"):
                return result.text.strip()
            return str(result).strip()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _fire_error(self, exc: Exception) -> None:
        try:
            self._on_error(exc)
        except Exception:
            log.exception("on_error callback raised")


class _MarkDoneCmd(_Command):
    """Sentinel: all chunks have been queued; transition back to READY."""

    pass


def _write_wav(path: str, audio: np.ndarray, sample_rate: int) -> None:
    """Write a float32 mono numpy array to a 16-bit PCM WAV file."""
    clamped = np.clip(audio, -1.0, 1.0)
    pcm = (clamped * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
