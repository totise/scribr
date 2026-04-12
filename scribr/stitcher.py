"""
stitcher.py — Overlap deduplication for chunked ASR output.

Problem:
  Each audio chunk is transcribed independently. Because consecutive chunks
  share an overlap window (e.g. 0.5 s), the same words appear at the tail of
  chunk N and the head of chunk N+1. We must remove those duplicates before
  typing so the user doesn't see repeated words.

Algorithm:
  Given the tail words of the previous chunk and the head words of the new
  chunk, find the longest suffix of the tail that matches a prefix of the new
  chunk (an "overlap seam"). If found with sufficient confidence, strip that
  prefix from the new chunk before returning it.

  Uses a sliding-window comparison with normalised word matching (lowercased,
  punctuation stripped) for robustness against minor capitalisation or
  punctuation differences at the boundary.

Example:
  Previous tail : "the quick brown fox"
  New chunk     : "brown fox jumps over"
  Overlap found : "brown fox"
  Returned text : "jumps over"

Edge cases:
  - No overlap found → return new chunk as-is (simple space join)
  - New chunk is entirely contained in the tail → return empty string (skip)
  - First chunk → return as-is (nothing to compare against)
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# How many words from each side to consider when searching for a seam.
# Large enough to catch overlaps reliably, small enough to avoid false matches.
WINDOW = 8

# Minimum number of words that must match to count as a real overlap.
MIN_MATCH = 2


def _normalise(word: str) -> str:
    """Lowercase and strip punctuation for comparison purposes."""
    return re.sub(r"[^\w]", "", word).lower()


def _words(text: str) -> list[str]:
    return text.split()


class Stitcher:
    """
    Stateful stitcher: call feed() for each successive chunk transcript.
    Call reset() at the start of each new recording session.
    """

    def __init__(self) -> None:
        self._prev_words: list[str] = []  # all words emitted so far
        self._chunk_index = 0

    def reset(self) -> None:
        """Call at the start of every new recording."""
        self._prev_words = []
        self._chunk_index = 0
        log.debug("Stitcher reset")

    def feed(self, chunk_text: str) -> str:
        """
        Accept the next chunk's raw transcript.
        Returns the deduplicated text safe to type (may be empty string if
        the entire chunk was already covered by the previous tail).
        """
        chunk_text = chunk_text.strip()
        self._chunk_index += 1

        if not chunk_text:
            log.debug("Chunk #%d empty — skipping", self._chunk_index)
            return ""

        new_words = _words(chunk_text)

        if not self._prev_words:
            # First chunk — nothing to stitch against
            self._prev_words = new_words
            log.debug("Chunk #%d (first): %r", self._chunk_index, chunk_text)
            return chunk_text

        # Find the longest suffix of prev_tail that matches a prefix of new_words
        prev_tail = self._prev_words[-WINDOW:]
        new_head = new_words[:WINDOW]

        overlap_len = _find_overlap(prev_tail, new_head)

        if overlap_len >= MIN_MATCH:
            deduped = new_words[overlap_len:]
            log.debug(
                "Chunk #%d: removed %d overlapping word(s) %r",
                self._chunk_index,
                overlap_len,
                new_words[:overlap_len],
            )
        else:
            deduped = new_words
            log.debug("Chunk #%d: no overlap found, using as-is", self._chunk_index)

        self._prev_words.extend(deduped)
        return " ".join(deduped)


def _find_overlap(tail: list[str], head: list[str]) -> int:
    """
    Find the longest n such that the last n words of `tail` match
    the first n words of `head` (case/punctuation-insensitive).
    Returns 0 if no match of length >= 1 is found.
    """
    tail_norm = [_normalise(w) for w in tail]
    head_norm = [_normalise(w) for w in head]

    best = 0
    max_check = min(len(tail_norm), len(head_norm))

    for n in range(max_check, 0, -1):
        # Does the last n words of tail match the first n words of head?
        if tail_norm[-n:] == head_norm[:n]:
            best = n
            break

    return best
