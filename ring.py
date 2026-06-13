"""
ring.py - Manages the list of known peers and derives ring topology.

Peers are tracked by nickname → (ip, last_seen_timestamp).
The ring order is alphabetical by nickname; each machine's successor is
the next machine in that sorted list (wrapping around).
"""

import time
import threading
import logging
from constants import HEARTBEAT_TIMEOUT

logger = logging.getLogger(__name__)


class Peer:
    """Represents a remote machine in the ring."""
    __slots__ = ("nickname", "ip", "last_seen")

    def __init__(self, nickname: str, ip: str):
        self.nickname  = nickname
        self.ip        = ip
        self.last_seen = time.monotonic()

    def refresh(self, ip: str) -> None:
        self.ip        = ip
        self.last_seen = time.monotonic()

    def is_alive(self) -> bool:
        return (time.monotonic() - self.last_seen) < HEARTBEAT_TIMEOUT


class RingTopology:
    """
    Thread-safe store of known peers.

    Call `add_or_refresh` when a valid HELLO is received.
    Call `prune_dead` periodically to remove timed-out peers.
    """

    def __init__(self, own_nickname: str, own_ip: str):
        self._own_nickname = own_nickname
        self._own_ip       = own_ip
        self._peers: dict[str, Peer] = {}   # nickname → Peer
        self._lock  = threading.Lock()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_or_refresh(self, nickname: str, ip: str) -> None:
        """Record or update a peer (ignores own nickname)."""
        if nickname == self._own_nickname:
            return
        with self._lock:
            if nickname in self._peers:
                self._peers[nickname].refresh(ip)
                logger.debug("Refreshed peer %s (%s)", nickname, ip)
            else:
                self._peers[nickname] = Peer(nickname, ip)
                logger.info("New peer discovered: %s (%s)", nickname, ip)

    def prune_dead(self) -> list[str]:
        """
        Remove peers that have not sent a HELLO within HEARTBEAT_TIMEOUT.
        Returns list of removed nicknames.
        """
        removed = []
        with self._lock:
            dead = [n for n, p in self._peers.items() if not p.is_alive()]
            for n in dead:
                del self._peers[n]
                removed.append(n)
                logger.warning("Peer %s timed out and was removed", n)
        return removed

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def sorted_nicknames(self) -> list[str]:
        """Return all nicknames (including own) in alphabetical order."""
        with self._lock:
            peers = list(self._peers.keys())
        return sorted(peers + [self._own_nickname])

    def successor_ip(self) -> str | None:
        """
        Return the IP of this machine's successor in the ring, or None if alone.
        """
        order = self.sorted_nicknames()
        if len(order) < 2:
            return None
        idx  = order.index(self._own_nickname)
        succ = order[(idx + 1) % len(order)]
        with self._lock:
            if succ == self._own_nickname:
                return None
            return self._peers[succ].ip

    def successor_nickname(self) -> str | None:
        """Return the nickname of this machine's successor, or None if alone."""
        order = self.sorted_nicknames()
        if len(order) < 2:
            return None
        idx  = order.index(self._own_nickname)
        succ = order[(idx + 1) % len(order)]
        return succ if succ != self._own_nickname else None

    def is_controller(self) -> bool:
        """True if this machine has the alphabetically smallest nickname in the ring."""
        return self.sorted_nicknames()[0] == self._own_nickname

    def ring_size(self) -> int:
        """Total number of machines currently in the ring (including self)."""
        with self._lock:
            return len(self._peers) + 1

    def get_ip(self, nickname: str) -> str | None:
        """Return the IP for *nickname*, or None if unknown."""
        if nickname == self._own_nickname:
            return self._own_ip
        with self._lock:
            p = self._peers.get(nickname)
            return p.ip if p else None

    def peer_count(self) -> int:
        """Number of remote peers known."""
        with self._lock:
            return len(self._peers)
