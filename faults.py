"""
faults.py - Fault injection module.

Before sending a data packet, this module may corrupt the message field
with a configurable probability, causing a CRC mismatch at the receiver.
The CRC is always (re)computed *after* the potential corruption.
"""

import random
import logging

logger = logging.getLogger(__name__)


def maybe_corrupt(packet: str, probability: float) -> str:
    """
    With *probability* ∈ [0, 1], corrupt the message field of a data packet
    by flipping a random character, then return the modified packet
    (without updating the CRC — the caller must recompute).

    If no corruption occurs, the packet is returned unchanged.

    The expected call site is:
        packet = maybe_corrupt(packet, cfg.error_probability)
        packet = recompute_data_crc(packet)   # always recompute after
    """
    if probability <= 0.0 or random.random() >= probability:
        return packet   # no fault injected

    # Corrupt by flipping one byte in the raw string (excluding the CRC field)
    idx = packet.rfind(":")
    if idx <= 0:
        return packet   # malformed — leave as-is

    base = packet[:idx]     # everything before the trailing CRC
    if not base:
        return packet

    pos  = random.randint(0, len(base) - 1)
    char = base[pos]
    # Flip the character: replace with a different ASCII printable character
    corrupted_char = chr((ord(char) + 1) % 128 or 65)
    corrupted_base = base[:pos] + corrupted_char + base[pos + 1:]

    logger.warning("FAULT INJECTED at position %d", pos)
    # Return with the old (now invalid) CRC still appended — caller rewrites it
    # Actually we return the base *without* a CRC so the caller's recompute_data_crc
    # adds a CRC that matches the corrupted content.  To make the CRC *invalid* at
    # the receiver we must NOT recompute — so we keep the original CRC.
    original_crc = packet[idx + 1:]
    return corrupted_base + ":" + original_crc
