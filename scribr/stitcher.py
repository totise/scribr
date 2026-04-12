"""
stitcher.py — Overlap deduplication for chunked ASR output.

Problem:
  Each audio chunk is transcribed independently. Because consecutive chunks
  share an overlap window (e.g. 0.5 s), the same words appear at the tail of
  chunk N and the head of chunk N+1. We must remove those duplicates before
  typing so the user doesn't see repeated words.

Algorithm (scored-ratio, after HuggingFace Transformers ASR pipeline):
  For each candidate overlap length n (from WINDOW down to MIN_MATCH):
    score(n) = matches(tail[-n:], head[:n]) / n  +  n / 10_000

  Where matches() counts the number of aligned equal tokens as returned by
  difflib.SequenceMatcher.  The small n/10_000 bias breaks ties in favour of
  longer overlaps, which empirically reduces hallucinated insertions.

  The candidate with the highest score whose match fraction exceeds
  MIN_SCORE_RATIO is selected.  If none qualifies, the chunk is returned as-is.

  This is strictly better than exact matching: it handles the common case where
  the ASR engine transcribes the overlap region with one or two word differences
  across consecutive chunks (e.g. due to context shift).

  No external dependencies — only difflib (stdlib).

Example (exact):
  Previous tail : "the quick brown fox"
  New chunk     : "brown fox jumps over"
  Overlap found : "brown fox"
  Returned text : "jumps over"

Example (fuzzy — one word differs in the overlap):
  Previous tail : "I said hello to her"
  New chunk     : "hello to him and then left"
  Overlap found : "hello to [her≈him]"  (2/3 match ratio ≈ 0.67 — accepted)
  Returned text : "and then left"

Edge cases:
  - No overlap found → return new chunk as-is (simple space join)
  - New chunk is entirely contained in the tail → return empty string (skip)
  - First chunk → return as-is (nothing to compare against)
"""

from __future__ import annotations

import difflib
import logging
import re

log = logging.getLogger(__name__)

# How many words from each side to consider when searching for a seam.
WINDOW = 8

# Minimum number of words that must match to count as a real overlap.
MIN_MATCH = 2

# Minimum fraction of words in the candidate window that must match.
# 0.50 → at least half of the words must be equal (after normalisation).
MIN_SCORE_RATIO = 0.50


def _normalise(word: str) -> str:
    """Lowercase and strip punctuation for comparison purposes."""
    return re.sub(r"[^\w]", "", word).lower()


def _words(text: str) -> list[str]:
    return text.split()


def _count_matches(a: list[str], b: list[str]) -> int:
    """
    Count the number of equal elements between two equal-length sequences
    using difflib.SequenceMatcher matching blocks.
    """
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(triple.size for triple in sm.get_matching_blocks())


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

        # Find the best-scoring overlap seam between tail and head.
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
    Find the overlap length n that maximises the scored-ratio:
        score(n) = matches / n  +  n / 10_000

    Only candidates where (matches / n) >= MIN_SCORE_RATIO are eligible.
    Returns 0 if no qualifying overlap of length >= MIN_MATCH is found.
    """
    tail_norm = [_normalise(w) for w in tail]
    head_norm = [_normalise(w) for w in head]

    max_check = min(len(tail_norm), len(head_norm))

    best_n = 0
    best_score = -1.0

    for n in range(MIN_MATCH, max_check + 1):
        matches = _count_matches(tail_norm[-n:], head_norm[:n])
        ratio = matches / n
        if ratio < MIN_SCORE_RATIO:
            continue
        score = ratio + n / 10_000
        if score > best_score:
            best_score = score
            best_n = n

    return best_n
