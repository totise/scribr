"""
pipeline.py — Recording-to-transcription pipeline strategies.

Two concrete pipelines share the same interface so app.py is strategy-agnostic:

  BatchPipeline
    Hold key → full audio accumulated → released → single inference → on_result

  ChunkedPipeline
    Hold key → chunks fired every step_seconds → each chunk transcribed in order
             → stitched result typed as it arrives → final tail on release

    Two overlap-removal strategies are chosen automatically based on the loaded
    model's capabilities:

    * TDT models (e.g. parakeet-tdt-0.6b-v2) return word-level timestamps.
      ChunkedPipeline uses timestamp-based trimming: only words whose midpoint
      falls after the overlap window boundary are kept.  This is the most
      robust approach and requires no text alignment at all.

    * RNNT models (e.g. parakeet-rnnt-110m-da-dk) return text only.
      ChunkedPipeline falls back to the scored-ratio Stitcher, which uses
      difflib.SequenceMatcher to find and remove the overlapping prefix.

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
from .transcriber import ChunkResult, Transcriber, TranscriberState

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
        self._transcriber._on_partial_result = lambda r, i: None  # unused

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
    removes overlap from results, and types text as it arrives.

    Overlap removal strategy:
      - TDT models (supports_timestamps=True):  timestamp trimming
        Words whose midpoint falls within the overlap window are dropped;
        no text alignment needed.
      - RNNT models (supports_timestamps=False): scored-ratio stitching
        difflib.SequenceMatcher finds and removes the duplicated prefix.
    """

    def __init__(
        self,
        transcriber: Transcriber,
        chunk_seconds: float = 2.5,
        overlap_seconds: float = 0.5,
    ) -> None:
        super().__init__(transcriber)
        self._overlap_seconds = overlap_seconds
        self._step_seconds = chunk_seconds - overlap_seconds
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
        # Chunk i starts at i * step_seconds from the recording start.
        # chunk_start=0 for the first chunk; subsequent chunks start at step_seconds
        # intervals (they include overlap_seconds of previous audio at the front).
        chunk_start = chunk_id * self._step_seconds
        log.debug(
            "ChunkedPipeline: queuing chunk #%d (start=%.2fs)", chunk_id, chunk_start
        )
        self._transcriber.transcribe_chunk(audio, chunk_id, chunk_start=chunk_start)

    def _on_tail(self, audio: np.ndarray) -> None:
        """Fired by recorder on key release with remaining audio."""
        with self._lock:
            self._active = False
            chunk_id = self._chunk_id
            self._chunk_id += 1

        chunk_start = chunk_id * self._step_seconds
        log.debug(
            "ChunkedPipeline: queuing tail chunk #%d (start=%.2fs)",
            chunk_id,
            chunk_start,
        )
        self._emit_state(PipelineState.TRANSCRIBING)
        self._transcriber.transcribe_chunk(audio, chunk_id, chunk_start=chunk_start)
        # Tell the transcriber this is the last chunk so it returns to READY
        self._transcriber.mark_chunks_done()

    # ------------------------------------------------------------------
    # Transcriber callback
    # ------------------------------------------------------------------

    def _on_partial_result(self, result: ChunkResult, chunk_id: int) -> None:
        """Called by transcriber for each completed chunk inference."""
        stitched = self._deduplicate(result)
        log.debug(
            "Chunk #%d partial result (raw=%r, stitched=%r)",
            chunk_id,
            result.text,
            stitched,
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

    def _deduplicate(self, result: ChunkResult) -> str:
        """
        Remove the overlap prefix from a chunk result.

        Strategy selection:
          - If the model returned word timestamps (TDT): trim by time.
            Keep only words whose midpoint is at or after
            (chunk_start + overlap_seconds).  For the first chunk (chunk_start=0)
            all words pass since no overlap was prepended.
          - Otherwise (RNNT): delegate to the scored-ratio Stitcher.
        """
        if result.has_timestamps:
            return self._trim_by_timestamps(result)
        return self._stitcher.feed(result.text)

    def _trim_by_timestamps(self, result: ChunkResult) -> str:
        """
        Keep only words whose midpoint falls at or after the trim boundary.

        Trim boundary = chunk_start + overlap_seconds.
        For the very first chunk (chunk_start == 0.0) the boundary is
        overlap_seconds, but since the first chunk has no preceding audio the
        overlap window is actually empty — so we treat it as 0.0.
        """
        # First chunk: chunk_start == 0 means the audio starts from t=0
        # with no preceding overlap, so no trimming needed.
        if result.chunk_start == 0.0:
            trim_at = 0.0
        else:
            trim_at = result.chunk_start + self._overlap_seconds

        kept = []
        for w in result.words:
            midpoint = (w.start + w.end) / 2.0
            if midpoint >= trim_at:
                kept.append(w.word)

        text = " ".join(kept)
        log.debug(
            "Timestamp trim: boundary=%.2fs, kept %d/%d words",
            trim_at,
            len(kept),
            len(result.words),
        )
        return text


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
