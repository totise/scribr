"""
main.py — Entry point for Scribr.

Sets up logging, checks macOS Accessibility permission, then starts the app.
"""

from __future__ import annotations

import logging
import sys


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    # Suppress noisy NeMo / torch output at INFO level
    for noisy in ("nemo", "torch", "numba", "matplotlib", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    _setup_logging()
    log = logging.getLogger(__name__)
    log.info("Starting Scribr")

    # macOS Accessibility check (non-fatal — user may dismiss and re-run)
    try:
        from .permissions import ensure_accessibility  # noqa: PLC0415

        ensure_accessibility()
    except Exception:
        log.warning("Could not check Accessibility permission", exc_info=True)

    from .app import ScribrApp  # noqa: PLC0415

    ScribrApp().run()


if __name__ == "__main__":
    main()
