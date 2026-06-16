"""
node.py - Core ring node logic.

Responsibilities:
  - Discovery (DISCOVER broadcast + HELLO collection)
  - Heartbeat (periodic HELLO + peer pruning)
  - Packet dispatch: routes incoming UDP datagrams to the right handler
  - Token handling: forward or send pending data
  - Data handling: intermediate forwarding, destination processing, origin confirmation
  - Token controller lifecycle management
"""

import socket
import time
import threading
import logging

from config          import Config
from constants       import (PKT_DISCOVER, PKT_HELLO, PKT_TOKEN, PKT_DATA,
                              FLAG_NONE, FLAG_ACK, FLAG_NAK,
                              DISCOVER_WAIT, DISCOVER_RETRY,
                              HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT)
from network         import UDPSocket
from ring            import RingTopology
from queue_manager   import MessageQueue, SequenceTracker
from crc             import (build_hello, verify_hello, build_data_packet,
                              verify_data_packet, recompute_data_crc,
                              parse_data_packet)
from faults          import maybe_corrupt
from token_controller import TokenController

logger = logging.getLogger(__name__)


def get_own_ip() -> str:
    """Return this machine's primary non-loopback IPv4 address."""
    # UDP connect trick: no packet is sent; the OS picks the source interface
    # based on the routing table. Try multiple targets so we find a real
    # interface even when there is no internet route.
    for target in ("8.8.8.8", "192.168.1.1", "192.168.0.1", "10.0.0.1", "172.16.0.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except OSError:
            pass
    # Last resort: scan addresses registered under the hostname
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


class Node:
    """
    Represents one machine in the token ring.

    Usage:
        node = Node(config)
        node.start()          # blocks; run in main thread or a dedicated thread
    """

    def __init__(self, cfg: Config):
        self.cfg      = cfg
        self.nickname = cfg.nickname
        self.ip       = get_own_ip()

        self.ring     = RingTopology(self.nickname, self.ip)
        self.out_q    = MessageQueue()
        self.seq_tr   = SequenceTracker()
        self.net      = UDPSocket(on_receive=self._on_packet)

        # Whether this node currently holds the token
        self._has_token      = False
        self._token_lock     = threading.Lock()

        # Whether we are waiting for a data packet to return
        self._waiting_for_data = False
        self._pending_packet   = None   # raw packet string in transit

        # Token controller (only active when this node is the ring controller)
        self._token_ctrl: TokenController | None = None
        self._ctrl_lock  = threading.Lock()

        # Heartbeat timer
        self._heartbeat_timer: threading.Timer | None = None
        self._prune_timer:     threading.Timer | None = None

        self._running = False

    # ==================================================================
    # Lifecycle
    # ==================================================================

    def start(self) -> None:
        """Start the node: discovery phase, then enter the event loop."""
        self._running = True
        self.net.start()
        logger.info("Node %s started (IP: %s)", self.nickname, self.ip)

        self._discover()
        self._schedule_heartbeat()
        self._schedule_prune()

        # If we are the alphabetically first and have peers, become controller.
        # _become_controller() will generate the token automatically.
        if self.ring.is_controller() and self.ring.peer_count() > 0:
            self._become_controller()

        # Run input loop in a background thread so docker exec -i works cleanly
        input_thread = threading.Thread(target=self._input_loop, daemon=True,
                                        name="input-loop")
        input_thread.start()

        # Main thread: keep alive until stopped
        try:
            while self._running:
                time.sleep(0.2)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        self._running = False
        if self._heartbeat_timer:
            self._heartbeat_timer.cancel()
        if self._prune_timer:
            self._prune_timer.cancel()
        if self._token_ctrl:
            self._token_ctrl.stop()
        self.net.stop()
        logger.info("Node %s stopped", self.nickname)

    # ==================================================================
    # Discovery
    # ==================================================================

    def _discover(self) -> None:
        """Send DISCOVER broadcast and collect HELLOs for DISCOVER_WAIT seconds."""
        while True:
            discover_pkt = f"{PKT_DISCOVER}:{self.nickname}:{self.ip}"
            logger.info("Sending DISCOVER broadcast")
            self.net.send_broadcast(discover_pkt)

            deadline = time.monotonic() + DISCOVER_WAIT
            while time.monotonic() < deadline:
                time.sleep(0.05)

            if self.ring.peer_count() > 0:
                break   # at least one peer found

            # No peers yet: wait longer before retrying
            logger.info("No peers found; retrying DISCOVER in %.0fs", DISCOVER_RETRY - DISCOVER_WAIT)
            time.sleep(DISCOVER_RETRY - DISCOVER_WAIT)

    # ==================================================================
    # Heartbeat
    # ==================================================================

    def _send_hello(self) -> None:
        hello = build_hello(self.nickname, self.ip)
        self.net.send_broadcast(hello)

    def _schedule_heartbeat(self) -> None:
        if not self._running:
            return
        self._send_hello()
        self._heartbeat_timer = threading.Timer(HEARTBEAT_INTERVAL, self._schedule_heartbeat)
        self._heartbeat_timer.daemon = True
        self._heartbeat_timer.start()

    def _schedule_prune(self) -> None:
        if not self._running:
            return
        removed = self.ring.prune_dead()
        if removed:
            logger.warning("Pruned inactive peers: %s", removed)
            self._recheck_controller()
        self._prune_timer = threading.Timer(HEARTBEAT_TIMEOUT / 3, self._schedule_prune)
        self._prune_timer.daemon = True
        self._prune_timer.start()

    # ==================================================================
    # Token controller lifecycle
    # ==================================================================

    def _recheck_controller(self) -> None:
        """Called when the ring topology changes; adjust controller status."""
        should_control = self.ring.is_controller() and self.ring.peer_count() > 0
        with self._ctrl_lock:
            currently_controlling = (self._token_ctrl is not None
                                      and self._token_ctrl.is_active())
        # Act outside the lock to avoid deadlock with _become_controller
        if should_control and not currently_controlling:
            self._become_controller()
        elif not should_control and currently_controlling:
            with self._ctrl_lock:
                if self._token_ctrl:
                    self._token_ctrl.stop()

    def _become_controller(self) -> None:
        """Start the token controller for this node and generate the first token."""
        ctrl = TokenController(
            token_timeout  = self.cfg.token_timeout,
            min_interval   = self.cfg.min_token_interval,
            on_token_lost  = self._on_token_lost,
            on_token_duplicate = self._on_token_duplicate,
        )
        with self._ctrl_lock:
            if self._token_ctrl:
                self._token_ctrl.stop()
            self._token_ctrl = ctrl
        ctrl.start()
        logger.info("This node is now the ring controller — generating token")
        # Small delay so the topology has settled before the token starts circulating
        threading.Timer(0.2, self._send_token).start()

    def _on_token_lost(self) -> None:
        """Generate a new token when the controller detects a timeout."""
        logger.warning("Token lost — generating new token")
        self._send_token()

    def _on_token_duplicate(self) -> None:
        """Log when the controller discards a duplicate token."""
        logger.warning("Duplicate token discarded by controller")

    # ==================================================================
    # Packet dispatch (runs in receive thread)
    # ==================================================================

    def _on_packet(self, data: str, addr: tuple) -> None:
        """Route an incoming UDP datagram to the appropriate handler."""
        src_ip = addr[0]

        if data.startswith(PKT_DISCOVER + ":"):
            self._handle_discover(data, src_ip)

        elif data.startswith(PKT_HELLO + ":"):
            self._handle_hello(data, src_ip)

        elif data == PKT_TOKEN:
            self._handle_token()

        elif data.startswith(PKT_TOKEN + ":"):
            # Token-like packet with extra fields → discard (spec §Token)
            logger.warning("Discarding malformed token-like packet: %s", data)

        elif data.startswith(PKT_DATA + ":"):
            self._handle_data(data)

        else:
            logger.debug("Unknown packet ignored: %s", data[:80])

    # ------------------------------------------------------------------
    # DISCOVER / HELLO handlers
    # ------------------------------------------------------------------

    def _handle_discover(self, packet: str, src_ip: str) -> None:
        """Reply to a DISCOVER with our HELLO broadcast."""
        parts = packet.split(":")
        if len(parts) != 3:
            return
        _, src_nick, _ = parts
        logger.info("DISCOVER from %s (%s) — sending HELLO", src_nick, src_ip)
        self.ring.add_or_refresh(src_nick, src_ip)
        self._send_hello()
        self._recheck_controller()

    def _handle_hello(self, packet: str, src_ip: str) -> None:
        """Process a HELLO: validate CRC, update peer table."""
        if not verify_hello(packet):
            logger.debug("HELLO with invalid CRC discarded")
            return
        parts = packet.split(":")
        if len(parts) != 4:
            return
        _, src_nick, advertised_ip, _ = parts
        # Prefer the actual UDP source address over the advertised one.
        # advertised_ip can be wrong (e.g. 127.0.0.1) when the sender's
        # get_own_ip() fails due to no internet route.
        ip = src_ip if (advertised_ip.startswith("127.") or not advertised_ip) else advertised_ip
        self.ring.add_or_refresh(src_nick, ip)
        self._recheck_controller()

    # ------------------------------------------------------------------
    # Token handler
    # ------------------------------------------------------------------

    def _handle_token(self) -> None:
        """Process receipt of the token."""
        logger.info("Token received")

        # Controller duplicate check
        with self._ctrl_lock:
            ctrl = self._token_ctrl
        if ctrl and ctrl.is_active():
            if not ctrl.token_seen():
                return   # duplicate — discard

        time.sleep(self.cfg.token_delay)

        pending = self.out_q.peek()
        if pending is None:
            # Nothing to send — pass token along
            self._send_token()
            return

        # Build and send data packet, then wait for it to return
        ttl    = self.ring.ring_size() * 2
        packet = build_data_packet(
            src=self.nickname, dst=pending.destination,
            flag=FLAG_NONE, seq=pending.seq,
            ttl=ttl, message=pending.content,
        )
        # Fault injection
        packet = maybe_corrupt(packet, self.cfg.error_probability)

        self._waiting_for_data = True
        self._pending_packet   = packet
        succ_ip = self.ring.successor_ip()
        if succ_ip:
            time.sleep(self.cfg.data_delay)
            self.net.send_unicast(succ_ip, packet)
            logger.info("Sent data packet seq=%d → %s", pending.seq, pending.destination)
        else:
            # Alone in the ring: can't send; pass token (which will never arrive)
            self._waiting_for_data = False

    # ------------------------------------------------------------------
    # Data packet handler
    # ------------------------------------------------------------------

    def _handle_data(self, packet: str) -> None:
        """Route a data packet: intermediate, destination, or origin."""
        # CRC check at every hop
        if not verify_data_packet(packet):
            logger.warning("Data packet CRC invalid — discarding (fires token timeout)")
            return

        parsed = parse_data_packet(packet)
        if parsed is None:
            logger.warning("Unparseable data packet — discarding")
            return

        if parsed["ttl"] <= 0:
            logger.warning("TTL=0 — discarding packet from %s", parsed["src"])
            return

        if parsed["dst"] == self.nickname:
            self._process_as_destination(packet, parsed)
        elif parsed["src"] == self.nickname:
            self._process_as_origin(packet, parsed)
        else:
            self._forward_intermediate(packet, parsed)

    def _forward_intermediate(self, packet: str, parsed: dict) -> None:
        """Decrement TTL, recompute CRC, forward to successor."""
        new_ttl  = parsed["ttl"] - 1
        new_pkt  = packet[:packet.index(":" + str(parsed["ttl"]) + ":")] \
                   + packet[packet.index(":" + str(parsed["ttl"]) + ":"):]
        # Rebuild cleanly from parsed fields
        new_pkt = build_data_packet(
            src=parsed["src"], dst=parsed["dst"], flag=parsed["flag"],
            seq=parsed["seq"], ttl=new_ttl, message=parsed["message"],
        )
        succ_ip = self.ring.successor_ip()
        if succ_ip:
            time.sleep(self.cfg.data_delay)
            self.net.send_unicast(succ_ip, new_pkt)

    def _process_as_destination(self, packet: str, parsed: dict) -> None:
        """Handle a packet addressed to this node."""
        # CRC already verified above; decide ACK or NAK
        crc_ok = True   # we already checked; NAK only if CRC was bad (already discarded)

        new_ttl = self.ring.ring_size() * 2

        accepted = self.seq_tr.accept(parsed["src"], parsed["seq"])
        if accepted:
            print(f"[MSG] {parsed['src']} → {self.nickname}: {parsed['message']}")
            flag = FLAG_ACK
        else:
            logger.debug("Duplicate seq=%d from %s — ACK without printing", parsed["seq"], parsed["src"])
            flag = FLAG_ACK   # spec: duplicate → ACK without printing

        new_pkt = build_data_packet(
            src=parsed["src"], dst=parsed["dst"], flag=flag,
            seq=parsed["seq"], ttl=new_ttl, message=parsed["message"],
        )
        succ_ip = self.ring.successor_ip()
        if succ_ip:
            self.net.send_unicast(succ_ip, new_pkt)
            logger.info("Sent %s for seq=%d from %s", flag, parsed["seq"], parsed["src"])

    def _process_as_origin(self, packet: str, parsed: dict) -> None:
        """Handle a packet that has returned to its origin."""
        self._waiting_for_data = False
        flag = parsed["flag"]

        # Treat CRC-already-verified packet: if flag is still maquinainexistente after
        # the full trip, the destination was not found.
        if flag == FLAG_ACK:
            print(f"[ACK] Message seq={parsed['seq']} delivered to {parsed['dst']}")
            self.out_q.confirm()
        elif flag == FLAG_NONE:
            print(f"[NOROUTE] {parsed['dst']} not found — message seq={parsed['seq']} dropped")
            self.out_q.confirm()
        elif flag == FLAG_NAK:
            print(f"[NAK] Delivery failed for seq={parsed['seq']} — will retransmit")
            # keep message in queue (do not call confirm)

        # Pass the token regardless
        self._send_token()

    # ==================================================================
    # Token sending
    # ==================================================================

    def _send_token(self) -> None:
        """Forward the token to the successor."""
        succ_ip = self.ring.successor_ip()
        if not succ_ip:
            logger.debug("Alone in ring — holding token")
            return
        time.sleep(self.cfg.token_delay)
        self.net.send_unicast(succ_ip, PKT_TOKEN)
        logger.debug("Token forwarded to %s", self.ring.successor_nickname())

    # ==================================================================
    # User input loop
    # ==================================================================

    def _input_loop(self) -> None:
        """Read messages from stdin and enqueue them."""
        print(f"\n=== Ring Node {self.nickname} ({self.ip}) ready ===")
        print("Type  <destination> <message>  to send a message.")
        print("Type  quit  to exit.\n")

        while self._running:
            try:
                line = input("> ").strip()
            except EOFError:
                break

            if not line:
                continue
            if line.lower() == "quit":
                self.stop()
                break

            parts = line.split(" ", 1)
            if len(parts) != 2:
                print("Usage: <destination> <message>")
                continue

            dst, msg = parts
            if ":" in dst or ":" in msg:
                print("Error: destination and message must not contain ':'")
                continue

            ok = self.out_q.enqueue(dst, msg)
            if ok:
                print(f"Enqueued → {dst}: {msg!r}  (queue size: {self.out_q.size()})")
            else:
                print("Queue full — message not enqueued")
