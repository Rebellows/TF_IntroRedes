"""
token_controller.py - Token timeout and duplicate detection.

Only the ring controller (alphabetically first machine) runs this.
When a different machine becomes controller, the previous one must call stop().
"""

import time
import threading
import logging

logger = logging.getLogger(__name__)


class TokenController:
    """
    Monitors token circulation for the ring controller.

    Two checks:
    1. Token lost  — if the token does not pass within `token_timeout` seconds,
                     call `on_token_lost()` to generate a new token.
    2. Token dup   — if the token passes again in less than `min_interval` seconds,
                     call `on_token_duplicate()` to discard it.
    """

    def __init__(self, token_timeout: float, min_interval: float,
                 on_token_lost, on_token_duplicate):
        self._timeout       = token_timeout
        self._min_interval  = min_interval
        self._on_lost       = on_token_lost
        self._on_duplicate  = on_token_duplicate

        self._last_seen: float | None = None
        self._lock   = threading.Lock()
        self._active = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin monitoring. Resets internal state."""
        with self._lock:
            self._last_seen = time.monotonic()
            self._active    = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True,
                                        name="token-ctrl")
        self._thread.start()
        logger.info("TokenController started (timeout=%.1fs, min=%.1fs)",
                    self._timeout, self._min_interval)

    def stop(self) -> None:
        """Stop monitoring (called when this machine loses controller status)."""
        with self._lock:
            self._active = False
        logger.info("TokenController stopped")

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    # ------------------------------------------------------------------
    # Token event
    # ------------------------------------------------------------------

    def token_seen(self) -> bool:
        """
        Call this every time the token passes through this machine.

        Returns True  → token is legitimate, forward it.
        Returns False → token is a duplicate, discard it.
        """
        now = time.monotonic()
        with self._lock:
            if self._last_seen is not None:
                interval = now - self._last_seen
                if interval < self._min_interval:
                    logger.warning("Duplicate token detected (interval=%.3fs)", interval)
                    self._on_duplicate()
                    return False   # caller should discard
            self._last_seen = now
        return True   # legitimate token

    # ------------------------------------------------------------------
    # Internal monitor loop
    # ------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        """Background thread: fires on_token_lost if token is overdue."""
        while True:
            time.sleep(0.5)
            with self._lock:
                if not self._active:
                    break
                if self._last_seen is None:
                    continue
                overdue = (time.monotonic() - self._last_seen) > self._timeout
            if overdue:
                logger.warning("Token lost! Generating a new one.")
                self._on_lost()
                with self._lock:
                    self._last_seen = time.monotonic()   # reset after generating
        logger.debug("TokenController monitor loop exited")
