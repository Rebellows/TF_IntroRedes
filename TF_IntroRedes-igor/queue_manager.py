"""
queue_manager.py - Outgoing message queue and per-source sequence tracking.

Outgoing queue:
  - FIFO, max QUEUE_MAX items.
  - Messages are only removed after ACK or maquinainexistente.
  - On NAK, the message stays at the front (retransmit next token pass).

Incoming sequence tracking:
  - Maintains the next expected sequence number per remote origin.
  - Detects and discards duplicates.
"""

import threading
import logging
from dataclasses import dataclass, field
from collections import deque
from constants import QUEUE_MAX

logger = logging.getLogger(__name__)


@dataclass
class OutgoingMessage:
    """A message waiting in the outgoing queue."""
    destination: str
    content:     str
    seq:         int        # assigned at enqueue time, fixed for retransmissions


class MessageQueue:
    """
    Thread-safe outgoing message queue.

    Sequence numbers are per-machine and increase monotonically.
    The front message is retained until explicitly confirmed.
    """

    def __init__(self):
        self._queue:   deque[OutgoingMessage] = deque()
        self._next_seq: int = 0
        self._lock = threading.Lock()

    def enqueue(self, destination: str, content: str) -> bool:
        """
        Add a message to the queue.

        Returns True on success, False if the queue is full.
        """
        with self._lock:
            if len(self._queue) >= QUEUE_MAX:
                logger.warning("Queue full — message to %s dropped", destination)
                return False
            msg = OutgoingMessage(destination, content, self._next_seq)
            self._next_seq += 1
            self._queue.append(msg)
            logger.debug("Enqueued seq=%d → %s: %r", msg.seq, destination, content)
            return True

    def peek(self) -> OutgoingMessage | None:
        """Return the front message without removing it, or None if empty."""
        with self._lock:
            return self._queue[0] if self._queue else None

    def confirm(self) -> None:
        """Remove the front message after ACK or maquinainexistente."""
        with self._lock:
            if self._queue:
                msg = self._queue.popleft()
                logger.debug("Confirmed seq=%d → %s", msg.seq, msg.destination)

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._queue) == 0

    def size(self) -> int:
        with self._lock:
            return len(self._queue)


class SequenceTracker:
    """
    Tracks the next expected incoming sequence number per source nickname.

    Used by the destination to detect duplicate packets.
    """

    def __init__(self):
        self._expected: dict[str, int] = {}  # source → next expected seq
        self._lock = threading.Lock()

    def accept(self, source: str, seq: int) -> bool:
        """
        Decide whether to accept a packet.

        Returns True (new packet) or False (duplicate).
        Also advances the counter if accepted.
        """
        with self._lock:
            expected = self._expected.get(source, 0)
            if seq < expected:
                logger.debug("Duplicate seq=%d from %s (expected %d)", seq, source, expected)
                return False          # duplicate
            # Accept (seq == expected is normal; seq > expected means gap → also accept)
            self._expected[source] = seq + 1
            return True
