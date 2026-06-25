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

# Set to False to silence the packet dumps.
WIRE_DEBUG = True
# Our own nickname — used to suppress self-echo (our own HELLO/DISCOVER coming
# back from a virtual adapter, e.g. VirtualBox 192.168.56.x). Set by the node.
OWN_NICK = ""

_TYPE_NAME = {"10": "DISCOVER", "20": "HELLO", "1000": "TOKEN", "2000": "DADOS"}


def _format_packet_line(direction: str, addr: str, raw: str) -> str:
    """Render a packet as ONE line: direction, type, peer and the raw packet."""
    ptype = raw.split(":", 1)[0]
    label = _TYPE_NAME.get(ptype, "?")
    arrow = "<--" if direction == "RX" else "-->"
    return f"[{direction}] {arrow} {addr:<28} {label:<8} {raw}"


def _dump(direction: str, addr: str, raw: str) -> None:
    if not WIRE_DEBUG:
        return
    # Suprime o auto-eco: RX de HELLO/DISCOVER cuja origem é o NOSSO apelido
    # (nós mesmos voltando por um adaptador virtual). Não esconde o token/dados
    # nem o tráfego dos outros grupos.
    if direction == "RX" and OWN_NICK:
        parts = raw.split(":")
        if len(parts) >= 2 and parts[0] in ("10", "20") and parts[1] == OWN_NICK:
            return
    print(_format_packet_line(direction, addr, raw), flush=True)


class UDPSocket:
    """
    Two-socket UDP wrapper for the ring, tuned for interoperability.

    - main socket  : bound to ("", 6000). Receives EVERYTHING (broadcast +
                     unicast) and SENDS UNICAST (token / data). Because it is
                     bound to port 6000, our unicast packets have source port
                     6000 — so peers that reply / forward to the source port
                     reach us, and the token comes back. Essential for interop.
    - bcast socket : bound to (own_ip, 0) with SO_BROADCAST. Sends only
                     broadcast (HELLO / DISCOVER). Binding to own_ip forces the
                     datagram out the LAN interface, so it actually reaches the
                     other machines instead of leaking out a virtual adapter
                     (e.g. VirtualBox 192.168.56.x) — that is why "they don't
                     see me" happens. Source port is irrelevant for broadcasts.
    """

    def __init__(self, on_receive, own_ip: str = "", port: int = UDP_PORT):
        """
        Parameters
        ----------
        on_receive : callable(data: str, addr: tuple)
            Called from the receiver thread for every incoming UDP datagram.
        own_ip : str
            This machine's LAN IP. Broadcasts are sent bound to this IP so they
            exit the correct interface on a multi-homed host.
        port : int
            UDP port for all traffic. Defaults to 6000 (TF spec).
        """
        self._on_receive = on_receive
        self._own_ip     = own_ip
        self._port       = port

        # Main socket: recv (broadcast + unicast) AND unicast send, port 6000.
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind(("", port))

        # Broadcast send socket, bound to own_ip so HELLO/DISCOVER exit the LAN.
        self._bcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._bcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._bcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if own_ip:
            self._bcast_sock.bind((own_ip, 0))

        # Broadcast destinations: limited broadcast (floods the whole L2 out the
        # bound interface) plus the /24 directed broadcast as a fallback.
        self._bcast_targets = [BROADCAST_ADDR]
        directed = self._directed_broadcast(own_ip)
        if directed and directed not in self._bcast_targets:
            self._bcast_targets.append(directed)

        self._running = False
        self._thread  = threading.Thread(target=self._recv_loop, daemon=True,
                                         name="udp-recv")

    @staticmethod
    def _directed_broadcast(ip: str) -> str:
        """Return the /24 directed broadcast for *ip* (e.g. 10.32.160.115 →
        10.32.160.255), or '' if ip is empty/invalid."""
        if not ip or ip.startswith("127."):
            return ""
        octets = ip.split(".")
        if len(octets) != 4:
            return ""
        return ".".join(octets[:3] + ["255"])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background receive thread."""
        self._running = True
        self._thread.start()
        logger.debug("UDP receiver started on port %d", self._port)

    def stop(self) -> None:
        """Signal the receive loop to stop."""
        self._running = False
        self._sock.close()
        self._bcast_sock.close()

    def send_broadcast(self, message: str) -> None:
        """Send *message* as a UDP broadcast via the LAN-bound socket.

        Sent to 255.255.255.255 AND the /24 directed broadcast so it reaches
        peers regardless of which interface is the OS default.
        """
        data = message.encode("utf-8")
        for target in self._bcast_targets:
            try:
                self._bcast_sock.sendto(data, (target, self._port))
            except OSError as exc:
                logger.error("Broadcast to %s failed: %s", target, exc)
        logger.debug("BROADCAST → %s", message)
        _dump("TX", f"{BROADCAST_ADDR}:{self._port} (broadcast)", message)

    def send_unicast(self, ip: str, message: str) -> None:
        """Send *message* to a specific *ip* via UDP unicast."""
        data = message.encode("utf-8")
        try:
            self._sock.sendto(data, (ip, self._port))
            logger.debug("UNICAST → %s  %s", ip, message)
            _dump("TX", f"{ip}:{self._port} (unicast)", message)
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
                    _dump("RX", f"{addr[0]}:{addr[1]}", message)
                    self._on_receive(message, addr)
            except socket.timeout:
                continue
            except OSError:
                break   # socket was closed
        logger.debug("UDP receiver stopped")
