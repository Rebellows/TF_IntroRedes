"""
crc.py - CRC32/ISO-HDLC utilities.

Uses Python's binascii.crc32, which implements CRC32/ISO-HDLC:
  - Polynomial : 0xEDB88320 (reflected)
  - Initial seed: 0xFFFFFFFF
  - Final XOR  : 0xFFFFFFFF
  - Input/output reflection: yes
Result is an unsigned 32-bit decimal integer.
"""

import binascii


def compute_crc32(data: str) -> int:
    """Return CRC32 of *data* as an unsigned 32-bit integer."""
    raw = data.encode("utf-8")
    return binascii.crc32(raw) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# HELLO  packets
# ---------------------------------------------------------------------------

def build_hello(nickname: str, ip: str) -> str:
    """
    Build a HELLO packet string with a valid CRC.

    Format: 20:<nickname>:<ip>:<CRC32>
    CRC is computed over '20:<nickname>:<ip>:' (trailing colon, empty CRC field).
    """
    base = f"20:{nickname}:{ip}:"
    crc  = compute_crc32(base)
    return f"{base}{crc}"


def verify_hello(packet: str) -> bool:
    """Return True if the HELLO packet's CRC is valid."""
    parts = packet.split(":")
    if len(parts) != 4:
        return False
    # Re-build the base (first three fields + trailing colon)
    base = ":".join(parts[:3]) + ":"
    try:
        received_crc = int(parts[3])
    except ValueError:
        return False
    return compute_crc32(base) == received_crc


# ---------------------------------------------------------------------------
# Data packets
# ---------------------------------------------------------------------------

def build_data_packet(src: str, dst: str, flag: str, seq: int,
                      ttl: int, message: str) -> str:
    """
    Build a data packet string with a valid CRC.

    Format: 2000:<src>:<dst>:<flag>:<seq>:<ttl>:<message>:<CRC32>
    CRC is computed over all fields except CRC itself (trailing colon).
    """
    base = f"2000:{src}:{dst}:{flag}:{seq}:{ttl}:{message}:"
    crc  = compute_crc32(base)
    return f"{base}{crc}"


def verify_data_packet(packet: str) -> bool:
    """Return True if the data packet's CRC is valid."""
    # Packet ends in :<CRC32>; split from the right to isolate CRC
    idx = packet.rfind(":")
    if idx == -1:
        return False
    base = packet[:idx + 1]          # everything up to and including the colon
    try:
        received_crc = int(packet[idx + 1:])
    except ValueError:
        return False
    return compute_crc32(base) == received_crc


def recompute_data_crc(packet: str) -> str:
    """
    Replace the CRC field of an existing data packet with a freshly computed one.
    Use this after modifying flag or TTL before forwarding.
    """
    idx = packet.rfind(":")
    base = packet[:idx + 1]
    crc  = compute_crc32(base)
    return f"{base}{crc}"


def parse_data_packet(packet: str) -> dict | None:
    """
    Parse a data packet into a dict with keys:
      type, src, dst, flag, seq, ttl, message, crc
    Returns None if the packet cannot be parsed.
    """
    parts = packet.split(":")
    if len(parts) < 8:
        return None
    try:
        return {
            "type":    parts[0],
            "src":     parts[1],
            "dst":     parts[2],
            "flag":    parts[3],
            "seq":     int(parts[4]),
            "ttl":     int(parts[5]),
            # message may contain ':' — rejoin middle fields
            "message": ":".join(parts[6:-1]),
            "crc":     int(parts[-1]),
        }
    except (ValueError, IndexError):
        return None
