"""
transcriber.py — NeMo model loader and inference worker.

Design:
- One model is held in memory at a time.
- A background thread handles both loading and inference so the UI never blocks.
- Callers communicate via callbacks (on_state_change, on_result, on_error,
  on_partial_result).

Two transcription modes:
  transcribe(audio)                 — batch: fires on_result(text) when done
  transcribe_chunk(audio, chunk_id) — chunked: fires on_partial_result(chunk_result, chunk_id)

ChunkResult carries both the transcript text and, when the model supports it
(TDT architecture), word-level timestamps. The pipeline uses timestamps to trim
overlap regions precisely instead of relying on text stitching.

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
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

# Type aliases
StateCallback = Callable[["TranscriberState"], None]
ResultCallback = Callable[[str], None]
ErrorCallback = Callable[[Exception], None]

SAMPLE_RATE = 16_000  # Hz — required by all Parakeet models


@dataclass
class WordTimestamp:
    word: str
    start: float  # seconds relative to the start of the chunk
    end: float  # seconds relative to the start of the chunk


@dataclass
class ChunkResult:
    """
    Result of a single chunk inference.

    text        — full transcript for this chunk
    words       — per-word timestamps (only populated for TDT models)
    chunk_start — absolute start time of this chunk in the full recording (seconds)
    chunk_end   — absolute end time of this chunk in the full recording (seconds)
    has_timestamps — True if word-level timestamps are available
    """

    text: str
    words: list[WordTimestamp] = field(default_factory=list)
    chunk_start: float = 0.0
    chunk_end: float = 0.0

    @property
    def has_timestamps(self) -> bool:
        return len(self.words) > 0


# PartialResultCallback receives the full ChunkResult so the pipeline can use
# timestamps when available
PartialResultCallback = Callable[["ChunkResult", int], None]


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
    def __init__(self, audio: np.ndarray, chunk_id: int, chunk_start: float) -> None:
        self.audio = audio
        self.chunk_id = chunk_id
        self.chunk_start = chunk_start  # absolute start time in the full recording


class _UnloadCmd(_Command):
    pass


class _StopCmd(_Command):
    pass


class _MarkDoneCmd(_Command):
    """Sentinel: all chunks have been queued; transition back to READY."""

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
        t.transcribe(audio)

    Usage (chunked):
        t = Transcriber(on_partial_result=handle_partial, ...)
        t.start()
        t.load("nvidia/parakeet-rnnt-110m-da-dk")
        t.transcribe_chunk(audio, chunk_id=0, chunk_start=0.0)
        t.transcribe_chunk(tail,  chunk_id=1, chunk_start=2.0)
        t.mark_chunks_done()
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
        self._on_partial_result = on_partial_result or (lambda r, i: None)
        self._on_error = on_error or (lambda _: None)

        self._queue: queue.Queue[_Command] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._state = TranscriberState.IDLE
        self._model = None
        self._current_model_id: str | None = None
        # Whether the loaded model supports word-level timestamps (TDT only)
        self._supports_timestamps: bool = False

    # ------------------------------------------------------------------
    # Public API (thread-safe, non-blocking)
    # ------------------------------------------------------------------

    @property
    def state(self) -> TranscriberState:
        return self._state

    @property
    def current_model_id(self) -> str | None:
        return self._current_model_id

    @property
    def supports_timestamps(self) -> bool:
        """True when the loaded model can return word-level timestamps."""
        return self._supports_timestamps

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
        self._queue.put(_LoadCmd(model_id))

    def unload(self) -> None:
        self._queue.put(_UnloadCmd())

    def transcribe(self, audio: np.ndarray) -> None:
        """Batch mode — fires on_result(text)."""
        if self._state != TranscriberState.READY:
            log.warning("transcribe() called but state is %s — ignoring", self._state)
            return
        self._queue.put(_TranscribeCmd(audio))

    def transcribe_chunk(
        self,
        audio: np.ndarray,
        chunk_id: int,
        chunk_start: float = 0.0,
    ) -> None:
        """
        Chunked mode — fires on_partial_result(ChunkResult, chunk_id).
        chunk_start: absolute start time of this chunk in the full recording (seconds).
        """
        if self._state not in (TranscriberState.READY, TranscriberState.TRANSCRIBING):
            log.warning(
                "transcribe_chunk() called but state is %s — ignoring", self._state
            )
            return
        self._queue.put(_TranscribeChunkCmd(audio, chunk_id, chunk_start))

    def mark_chunks_done(self) -> None:
        """
        Signal that the final chunk has been queued. The worker will transition
        state back to READY after processing it.
        """
        self._queue.put(_MarkDoneCmd())

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
                self._do_transcribe_chunk(cmd.audio, cmd.chunk_id, cmd.chunk_start)
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
            self._supports_timestamps = _model_supports_timestamps(model)
            log.info(
                "Model ready: %s (timestamps=%s)",
                model_id,
                self._supports_timestamps,
            )
            self._set_state(TranscriberState.READY)
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
            self._supports_timestamps = False
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
        log.info("Transcribing %.1fs (batch)", len(audio) / SAMPLE_RATE)
        try:
            text = _extract_text(self._run_inference(audio, timestamps=False))
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

    def _do_transcribe_chunk(
        self, audio: np.ndarray, chunk_id: int, chunk_start: float
    ) -> None:
        if self._model is None:
            log.error("transcribe_chunk called with no model loaded")
            return
        if self._state == TranscriberState.READY:
            self._set_state(TranscriberState.TRANSCRIBING)
        chunk_end = chunk_start + len(audio) / SAMPLE_RATE
        log.info(
            "Transcribing chunk #%d (%.1fs–%.1fs, timestamps=%s)",
            chunk_id,
            chunk_start,
            chunk_end,
            self._supports_timestamps,
        )
        try:
            raw = self._run_inference(audio, timestamps=self._supports_timestamps)
            result = _build_chunk_result(raw, chunk_start, chunk_end)
            log.info(
                "Chunk #%d result: %r (words_with_ts=%d)",
                chunk_id,
                result.text,
                len(result.words),
            )
            try:
                self._on_partial_result(result, chunk_id)
            except Exception:
                log.exception("on_partial_result callback raised")
        except Exception as exc:
            log.exception("Chunk #%d transcription failed", chunk_id)
            self._set_state(TranscriberState.ERROR)
            self._fire_error(exc)

    def _run_inference(self, audio: np.ndarray, timestamps: bool = False):
        """Run model.transcribe() and return the raw NeMo output object."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            _write_wav(tmp_path, audio, SAMPLE_RATE)
            if timestamps:
                output = self._model.transcribe([tmp_path], timestamps=True)
            else:
                output = self._model.transcribe([tmp_path])
            return output[0]
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


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _model_supports_timestamps(model) -> bool:
    """
    Return True if this NeMo model is a TDT variant that outputs word timestamps.
    We detect this by checking the class name (EncDecTDTBPEModel / EncDecTDTModel)
    rather than attempting a probe inference.
    """
    class_name = type(model).__name__
    return "TDT" in class_name


def _extract_text(raw) -> str:
    """Extract the transcript string from a NeMo result object or plain string."""
    if hasattr(raw, "text"):
        return raw.text.strip()
    return str(raw).strip()


def _build_chunk_result(raw, chunk_start: float, chunk_end: float) -> ChunkResult:
    """
    Convert a NeMo transcribe() output item into a ChunkResult.
    Timestamps from NeMo are relative to the chunk's audio start (t=0),
    so we offset them by chunk_start to get absolute times.
    """
    text = _extract_text(raw)
    words: list[WordTimestamp] = []

    # TDT models return timestamps under result.timestamp['word']
    # Each entry: {'word': str, 'start': float, 'end': float}
    try:
        if hasattr(raw, "timestamp") and raw.timestamp:
            word_stamps = raw.timestamp.get("word", [])
            for w in word_stamps:
                words.append(
                    WordTimestamp(
                        word=w["word"],
                        start=w["start"] + chunk_start,
                        end=w["end"] + chunk_start,
                    )
                )
    except Exception:
        log.debug("Could not extract word timestamps from result", exc_info=True)

    return ChunkResult(
        text=text,
        words=words,
        chunk_start=chunk_start,
        chunk_end=chunk_end,
    )


def _write_wav(path: str, audio: np.ndarray, sample_rate: int) -> None:
    """Write a float32 mono numpy array to a 16-bit PCM WAV file."""
    clamped = np.clip(audio, -1.0, 1.0)
    pcm = (clamped * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
