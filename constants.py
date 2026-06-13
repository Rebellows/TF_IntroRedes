"""
constants.py - Shared constants for the ring network simulation.
"""

UDP_PORT = 6000
BROADCAST_ADDR = "255.255.255.255"

# Packet type codes
PKT_DISCOVER = "10"
PKT_HELLO    = "20"
PKT_TOKEN    = "1000"
PKT_DATA     = "2000"

# Data packet flags
FLAG_NONE     = "maquinainexistente"
FLAG_ACK      = "ACK"
FLAG_NAK      = "NAK"

# Heartbeat / discovery timings
DISCOVER_WAIT       = 1.0    # seconds to wait for HELLOs after DISCOVER
DISCOVER_RETRY      = 10.0   # seconds without any HELLO before re-sending DISCOVER
HEARTBEAT_INTERVAL  = 10.0   # seconds between HELLO broadcasts
HEARTBEAT_TIMEOUT   = 30.0   # seconds without HELLO before removing a peer

# Message queue capacity
QUEUE_MAX = 10
