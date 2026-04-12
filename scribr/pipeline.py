"""
pipeline.py — Recording-to-transcription pipeline strategies.

Two concrete pipelines share the same interface so app.py is strategy-agnostic:

  BatchPipeline
    Hold key → full audio accumulated → released → single inference → on_result

  ChunkedPipeline
    Hold key → chunks fired every step_seconds → each chunk transcribed in order
             → stitched result typed as it arrives → final tail on release

Both pipelines own their own Recorder instance (configured appropriately) and
share a reference to the single Transcriber that app.py manages.

Interface (used by app.py):
    pipeline.start_recording()       — called on hotkey press
    pipeline.stop_recording()        — called on hotkey release
    pipeline.on_result               — set by app.py; called with text to type
    pipeline.on_state_change         — set by app.py; called with PipelineState
    pipeline.is_active               — True while recording or transcribing

PipelineState mirrors the visual feedback the app needs:
    IDLE         — waiting
    RECORDING    — mic is open, no result yet
    RECORDING_TRANSCRIBING — chunked only: mic open AND a chunk is being processed
    TRANSCRIBING — key released, final inference running
    DONE         — result delivered, back to idle
"""

from __future__ import annotations

import logging
import threading
from enum import Enum, auto
from typing import Callable

import numpy as np

from .recorder import Recorder
from .stitcher import Stitcher
from .transcriber import Transcriber, TranscriberState

log = logging.getLogger(__name__)

ResultCallback = Callable[[str], None]


class PipelineState(Enum):
    IDLE = auto()
    RECORDING = auto()
    RECORDING_TRANSCRIBING = auto()  # chunked: recording + parallel inference
    TRANSCRIBING = auto()
    DONE = auto()


# ------------------------------------------------------------------
# Base
# ------------------------------------------------------------------


class _BasePipeline:
    def __init__(self, transcriber: Transcriber) -> None:
        self._transcriber = transcriber
        self.on_result: ResultCallback = lambda _: None
        self.on_state_change: Callable[[PipelineState], None] = lambda _: None
        self._lock = threading.Lock()

    def start_recording(self) -> None:
        raise NotImplementedError

    def stop_recording(self) -> None:
        raise NotImplementedError

    @property
    def is_active(self) -> bool:
        raise NotImplementedError

    def _emit_state(self, state: PipelineState) -> None:
        try:
            self.on_state_change(state)
        except Exception:
            log.exception("on_state_change callback raised")

    def _emit_result(self, text: str) -> None:
        if text:
            try:
                self.on_result(text)
            except Exception:
                log.exception("on_result callback raised")


# ------------------------------------------------------------------
# Batch pipeline
# ------------------------------------------------------------------


class BatchPipeline(_BasePipeline):
    """
    Simple hold-and-release: accumulates the full recording, then runs
    a single inference pass after the key is released.
    """

    def __init__(self, transcriber: Transcriber) -> None:
        super().__init__(transcriber)
        self._recorder = Recorder(on_complete=self._on_audio_ready)
        self._active = False

        # Wire transcriber result callback
        self._transcriber._on_result = self._on_transcription_result
        self._transcriber._on_partial_result = lambda t, i: None  # unused

    def start_recording(self) -> None:
        with self._lock:
            if self._active:
                return
            if self._transcriber.state != TranscriberState.READY:
                log.debug(
                    "BatchPipeline: transcriber not ready — ignoring record start"
                )
                return
            self._active = True

        self._emit_state(PipelineState.RECORDING)
        try:
            self._recorder.start()
        except Exception:
            log.exception("BatchPipeline: failed to start recorder")
            with self._lock:
                self._active = False
            self._emit_state(PipelineState.IDLE)

    def stop_recording(self) -> None:
        with self._lock:
            if not self._active:
                return
        self._recorder.stop()  # → _on_audio_ready

    @property
    def is_active(self) -> bool:
        return self._active

    def _on_audio_ready(self, audio: np.ndarray) -> None:
        with self._lock:
            self._active = False
        self._emit_state(PipelineState.TRANSCRIBING)
        self._transcriber.transcribe(audio)

    def _on_transcription_result(self, text: str) -> None:
        self._emit_state(PipelineState.DONE)
        self._emit_result(text)
        self._emit_state(PipelineState.IDLE)


# ------------------------------------------------------------------
# Chunked pipeline
# ------------------------------------------------------------------


class ChunkedPipeline(_BasePipeline):
    """
    Pseudo-streaming: fires chunks to the transcriber while recording,
    stitches partial results together, and types text as it arrives.
    """

    def __init__(
        self,
        transcriber: Transcriber,
        chunk_seconds: float = 2.5,
        overlap_seconds: float = 0.5,
    ) -> None:
        super().__init__(transcriber)
        self._stitcher = Stitcher()
        self._chunk_id = 0
        self._active = False
        self._has_partial = False  # True once ≥1 chunk result has arrived

        self._recorder = Recorder(
            on_chunk=self._on_chunk,
            on_complete=self._on_tail,
            chunk_seconds=chunk_seconds,
            overlap_seconds=overlap_seconds,
        )

        # Wire transcriber callbacks
        self._transcriber._on_partial_result = self._on_partial_result
        self._transcriber._on_result = lambda t: None  # unused

    def start_recording(self) -> None:
        with self._lock:
            if self._active:
                return
            if self._transcriber.state != TranscriberState.READY:
                log.debug(
                    "ChunkedPipeline: transcriber not ready — ignoring record start"
                )
                return
            self._active = True
            self._chunk_id = 0
            self._has_partial = False
            self._stitcher.reset()

        self._emit_state(PipelineState.RECORDING)
        try:
            self._recorder.start()
        except Exception:
            log.exception("ChunkedPipeline: failed to start recorder")
            with self._lock:
                self._active = False
            self._emit_state(PipelineState.IDLE)

    def stop_recording(self) -> None:
        with self._lock:
            if not self._active:
                return
        self._recorder.stop()  # → _on_tail (and flushes remaining audio)

    @property
    def is_active(self) -> bool:
        return self._active

    # ------------------------------------------------------------------
    # Recorder callbacks
    # ------------------------------------------------------------------

    def _on_chunk(self, audio: np.ndarray) -> None:
        """Fired by recorder every step_seconds while key is held."""
        with self._lock:
            chunk_id = self._chunk_id
            self._chunk_id += 1
        log.debug("ChunkedPipeline: queuing chunk #%d", chunk_id)
        self._transcriber.transcribe_chunk(audio, chunk_id)

    def _on_tail(self, audio: np.ndarray) -> None:
        """Fired by recorder on key release with remaining audio."""
        with self._lock:
            self._active = False
            chunk_id = self._chunk_id
            self._chunk_id += 1

        log.debug("ChunkedPipeline: queuing tail chunk #%d", chunk_id)
        self._emit_state(PipelineState.TRANSCRIBING)
        self._transcriber.transcribe_chunk(audio, chunk_id)
        # Tell the transcriber this is the last chunk so it returns to READY
        self._transcriber.mark_chunks_done()

    # ------------------------------------------------------------------
    # Transcriber callback
    # ------------------------------------------------------------------

    def _on_partial_result(self, text: str, chunk_id: int) -> None:
        """Called by transcriber for each completed chunk inference."""
        stitched = self._stitcher.feed(text)
        log.debug(
            "Chunk #%d partial result (raw=%r, stitched=%r)", chunk_id, text, stitched
        )

        with self._lock:
            was_first = not self._has_partial
            if stitched:
                self._has_partial = True
            still_recording = self._active

        if stitched:
            # Update icon: if still recording, show combined recording+transcribing state
            if still_recording or was_first:
                self._emit_state(PipelineState.RECORDING_TRANSCRIBING)
            self._emit_result(stitched)

        # If this is the tail chunk (recording already stopped) and the
        # transcriber will soon emit READY via mark_chunks_done, transition to DONE
        if not still_recording:
            self._emit_state(PipelineState.DONE)
            self._emit_state(PipelineState.IDLE)


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------


def make_pipeline(
    strategy: str,
    transcriber: Transcriber,
    chunk_seconds: float = 2.5,
    overlap_seconds: float = 0.5,
) -> _BasePipeline:
    """
    Instantiate the correct pipeline for the given strategy string.
    strategy: "batch" | "chunked"
    """
    if strategy == "chunked":
        log.info(
            "Creating ChunkedPipeline (chunk=%.1fs overlap=%.1fs)",
            chunk_seconds,
            overlap_seconds,
        )
        return ChunkedPipeline(
            transcriber,
            chunk_seconds=chunk_seconds,
            overlap_seconds=overlap_seconds,
        )
    else:
        if strategy != "batch":
            log.warning("Unknown strategy %r — falling back to batch", strategy)
        log.info("Creating BatchPipeline")
        return BatchPipeline(transcriber)
