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

# Set to False to silence the ASCII packet dumps.
WIRE_DEBUG = True


def _decode_packet(raw: str) -> list[tuple[str, str]]:
    """Break a raw packet into (field_name, value) pairs for display.

    Works purely off the wire format so it shows EXACTLY what arrived —
    including malformed packets from other groups (wrong field count, etc.).
    """
    parts = raw.split(":")
    ptype = parts[0] if parts else ""

    if ptype == "10":          # DISCOVER  10:<nick>:<ip>
        names = ["tipo (10=DISCOVER)", "origem", "ip_origem"]
    elif ptype == "20":        # HELLO     20:<nick>:<ip>:<crc>
        names = ["tipo (20=HELLO)", "origem", "ip_origem", "CRC32"]
    elif ptype == "1000":      # TOKEN     1000
        names = ["tipo (1000=TOKEN)"]
    elif ptype == "2000":      # DATA      2000:src:dst:flag:seq:ttl:msg:crc
        names = ["tipo (2000=DADOS)", "origem", "destino", "flag",
                 "numero de sequencia", "TTL", "mensagem", "CRC32"]
        # message may contain ':' — collapse the middle back together
        if len(parts) > 8:
            parts = parts[:6] + [":".join(parts[6:-1])] + [parts[-1]]
    else:
        names = []

    rows = []
    for i, val in enumerate(parts):
        label = names[i] if i < len(names) else f"campo[{i}]?"
        rows.append((label, val))
    return rows


def _format_packet_ascii(direction: str, addr: str, raw: str) -> str:
    """Render a packet as an ASCII box, decomposed field by field."""
    rows  = _decode_packet(raw)
    arrow = "RECEBIDO de" if direction == "RX" else "ENVIADO  para"
    head  = f" {direction}  {arrow} {addr}"
    rawln = " raw: " + raw

    # Column widths for the field table
    klen = max([len(k) for k, _ in rows] + [5])
    vlen = max([len(v) for _, v in rows] + [5])
    # Inner width must fit the widest of: field row, header, raw line
    field_w = klen + vlen + 7   # " | " separators + padding
    inner   = max(field_w, len(head), len(rawln))

    top   = "+" + "-" * inner + "+"
    lines = [top]
    lines.append("|" + head.ljust(inner) + "|")
    lines.append("|" + rawln.ljust(inner) + "|")
    lines.append("+" + "-" * inner + "+")
    for k, v in rows:
        row = f" {k.ljust(klen)} | {v.ljust(vlen)} "
        lines.append("|" + row.ljust(inner) + "|")
    lines.append(top)
    return "\n".join(lines)


def _dump(direction: str, addr: str, raw: str) -> None:
    if not WIRE_DEBUG:
        return
    # Mostra TODOS os pacotes (DISCOVER, HELLO, token, dados), RX e TX.
    print(_format_packet_ascii(direction, addr, raw), flush=True)


class UDPSocket:
    """
    Thin wrapper around a UDP socket.

    - Sends broadcast (DISCOVER / HELLO) and unicast (token / data).
    - Runs a background receiver thread that delivers packets to a callback.

    own_ip: if provided, broadcast packets are sent bound to this source IP,
    ensuring they go out on the correct interface on multi-homed machines.
    """

    def __init__(self, on_receive, own_ip: str = ""):
        """
        Parameters
        ----------
        on_receive : callable(data: str, addr: tuple)
            Called from the receiver thread for every incoming UDP datagram.
        own_ip : str
            The IP of the interface to use for sending. If empty, the OS chooses.
        """
        self._on_receive = on_receive
        self._own_ip     = own_ip

        # Main socket: receives all UDP on port 6000 (bound to all interfaces)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind(("", UDP_PORT))

        # Separate send socket bound to own_ip so broadcasts exit the right interface
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if own_ip:
            self._send_sock.bind((own_ip, 0))   # 0 = let OS pick source port

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
        self._send_sock.close()

    def send_broadcast(self, message: str) -> None:
        """Send *message* as a UDP broadcast via the configured interface."""
        data = message.encode("utf-8")
        try:
            self._send_sock.sendto(data, (BROADCAST_ADDR, UDP_PORT))
            logger.debug("BROADCAST → %s", message)
            _dump("TX", f"{BROADCAST_ADDR}:{UDP_PORT} (broadcast)", message)
        except OSError as exc:
            logger.error("Broadcast failed: %s", exc)

    def send_unicast(self, ip: str, message: str) -> None:
        """Send *message* to a specific *ip* via UDP unicast."""
        data = message.encode("utf-8")
        try:
            self._send_sock.sendto(data, (ip, UDP_PORT))
            logger.debug("UNICAST → %s  %s", ip, message)
            _dump("TX", f"{ip}:{UDP_PORT} (unicast)", message)
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
