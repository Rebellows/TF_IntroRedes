"""
network.py - UDP socket abstraction for the ring network.

All sending and receiving goes through this module so the rest of the code
stays free of raw socket calls.
"""

import socket
import threading
import logging
from constants import UDP_PORT, BROADCAST_ADDR

logger = logging.getLogger(__name__)


class UDPSocket:
    """
    Thin wrapper around a UDP socket.

    - Sends broadcast (DISCOVER / HELLO) and unicast (token / data).
    - Runs a background receiver thread that delivers packets to a callback.
    """

    def __init__(self, on_receive):
        """
        Parameters
        ----------
        on_receive : callable(data: str, addr: tuple)
            Called from the receiver thread for every incoming UDP datagram.
        """
        self._on_receive = on_receive
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind(("", UDP_PORT))

        self._running = False
        self._thread  = threading.Thread(target=self._recv_loop, daemon=True,
                                         name="udp-recv")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background receive thread."""
        self._running = True
        self._thread.start()
        logger.debug("UDP receiver started on port %d", UDP_PORT)

    def stop(self) -> None:
        """Signal the receive loop to stop."""
        self._running = False
        self._sock.close()

    def send_broadcast(self, message: str) -> None:
        """Send *message* as a UDP broadcast."""
        data = message.encode("utf-8")
        try:
            self._sock.sendto(data, (BROADCAST_ADDR, UDP_PORT))
            logger.debug("BROADCAST → %s", message)
        except OSError as exc:
            logger.error("Broadcast failed: %s", exc)

    def send_unicast(self, ip: str, message: str) -> None:
        """Send *message* to a specific *ip* via UDP unicast."""
        data = message.encode("utf-8")
        try:
            self._sock.sendto(data, (ip, UDP_PORT))
            logger.debug("UNICAST → %s  %s", ip, message)
        except OSError as exc:
            logger.error("Unicast to %s failed: %s", ip, exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _recv_loop(self) -> None:
        """Continuously read datagrams and call the registered callback."""
        self._sock.settimeout(1.0)   # allows clean shutdown
        while self._running:
            try:
                raw, addr = self._sock.recvfrom(65535)
                message = raw.decode("utf-8", errors="replace").strip()
                if message:
                    self._on_receive(message, addr)
            except socket.timeout:
                continue
            except OSError:
                break   # socket was closed
        logger.debug("UDP receiver stopped")
