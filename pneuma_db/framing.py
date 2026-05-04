"""
PNEUMA Framing Layer — Layer 2
==============================
PNEUMA Packet Protocol (PPP) — packet structure, CRC, sequencing.

Packet format (total ≤ 280 bytes):
  MAGIC    4B  0x504E4D41  ('PNMA')
  VERSION  1B  0x01
  FLAGS    1B  bitmask
  SEQ      3B  sequence number 0–16M
  TOTAL    3B  total packets in message
  SRC      8B  source node ID hash
  DST      8B  destination node ID hash
  LEN      2B  payload length
  PAYLOAD  ≤240B encrypted+error-coded data
  CRC32    4B  CRC-32 of all preceding fields
  END      2B  0xFFFF
"""

import struct
import zlib
import time
import math
from dataclasses import dataclass, field
from typing import Optional, List
from enum import IntFlag


MAGIC       = b'PNMA'
VERSION     = 0x01
END_MARKER  = b'\xFF\xFF'
MAX_PAYLOAD = 240
HEADER_SIZE = 4+1+1+3+3+8+8+2       # = 30
FOOTER_SIZE = 4+2                     # crc32 + end
MAX_PACKET  = HEADER_SIZE + MAX_PAYLOAD + FOOTER_SIZE  # = 276


class Flags(IntFlag):
    NONE        = 0x00
    ACK_REQ     = 0x01   # sender wants acknowledgement
    FRAGMENT    = 0x02   # this packet is part of a multi-packet message
    ENCRYPTED   = 0x04   # payload is encrypted
    COMPRESSED  = 0x08   # payload is compressed (future)
    ACK         = 0x10   # this IS an acknowledgement
    PING        = 0x20   # ping packet
    PONG        = 0x40   # pong response


@dataclass
class Packet:
    seq:     int
    total:   int
    src:     bytes        # 8-byte node hash
    dst:     bytes        # 8-byte node hash
    payload: bytes
    flags:   Flags = Flags.NONE
    version: int   = VERSION

    def serialize(self) -> bytes:
        """Encode packet to bytes for transmission."""
        length = len(self.payload)
        header = (
            MAGIC
            + bytes([self.version])
            + bytes([int(self.flags)])
            + self.seq.to_bytes(3,   'big')
            + self.total.to_bytes(3, 'big')
            + self.src[:8].ljust(8, b'\x00')
            + self.dst[:8].ljust(8, b'\x00')
            + length.to_bytes(2, 'big')
            + self.payload
        )
        crc    = zlib.crc32(header) & 0xFFFFFFFF
        return header + struct.pack('>I', crc) + END_MARKER

    @staticmethod
    def deserialize(data: bytes) -> Optional['Packet']:
        """Decode bytes into a Packet. Returns None if invalid."""
        try:
            if not data.startswith(MAGIC):
                return None
            if not data.endswith(END_MARKER):
                return None

            # Strip END_MARKER and split CRC
            body_with_crc = data[:-2]
            body          = body_with_crc[:-4]
            given_crc     = struct.unpack('>I', body_with_crc[-4:])[0]

            if (zlib.crc32(body) & 0xFFFFFFFF) != given_crc:
                return None   # CRC mismatch — corrupted or tampered

            offset  = 4   # skip MAGIC
            version = body[offset];            offset += 1
            flags   = Flags(body[offset]);     offset += 1
            seq     = int.from_bytes(body[offset:offset+3], 'big');   offset += 3
            total   = int.from_bytes(body[offset:offset+3], 'big');   offset += 3
            src     = body[offset:offset+8];   offset += 8
            dst     = body[offset:offset+8];   offset += 8
            length  = int.from_bytes(body[offset:offset+2], 'big');   offset += 2
            payload = body[offset:offset+length]

            return Packet(
                seq=seq, total=total, src=src, dst=dst,
                payload=payload, flags=flags, version=version
            )
        except Exception:
            return None


class Framer:
    """
    Splits a payload into multiple packets and reassembles them.
    """

    def __init__(self, src_hash: bytes, max_payload: int = MAX_PAYLOAD):
        self.src_hash    = src_hash
        self.max_payload = max_payload

    def fragment(self, payload: bytes, dst_hash: bytes, flags: Flags = Flags.ENCRYPTED) -> List[Packet]:
        """Split payload into a list of Packets."""
        chunks = [
            payload[i:i + self.max_payload]
            for i in range(0, len(payload), self.max_payload)
        ] or [b'']

        total = len(chunks)
        return [
            Packet(
                seq     = i,
                total   = total,
                src     = self.src_hash,
                dst     = dst_hash,
                payload = chunk,
                flags   = flags | (Flags.FRAGMENT if total > 1 else Flags.NONE),
            )
            for i, chunk in enumerate(chunks)
        ]

    @staticmethod
    def reassemble(packets: List[Packet]) -> Optional[bytes]:
        """
        Reassemble an ordered list of packets into the original payload.
        Returns None if any packet is missing.
        """
        if not packets:
            return None

        total = packets[0].total
        if len(packets) != total:
            return None

        ordered = sorted(packets, key=lambda p: p.seq)
        return b''.join(p.payload for p in ordered)


class ReassemblyBuffer:
    """
    Collects incoming packets for multiple in-flight messages
    and returns complete messages as they finish.
    """

    def __init__(self, timeout: float = 30.0):
        self.timeout  = timeout
        self._buffers: dict[tuple, dict] = {}   # (src, seq_0) → {packets, started_at}

    def add_packet(self, packet: Packet) -> Optional[bytes]:
        """
        Add a packet. Returns reassembled payload when all fragments arrive,
        or None if the message is incomplete.
        """
        key = (bytes(packet.src), packet.total)
        now = time.time()

        if key not in self._buffers:
            self._buffers[key] = {'packets': {}, 'started_at': now}

        buf = self._buffers[key]
        buf['packets'][packet.seq] = packet

        # Expire old buffers
        self._expire(now)

        # Check if complete
        if len(buf['packets']) == packet.total:
            ordered = [buf['packets'][i] for i in range(packet.total)]
            del self._buffers[key]
            return b''.join(p.payload for p in ordered)

        return None

    def _expire(self, now: float):
        expired = [k for k, v in self._buffers.items()
                   if now - v['started_at'] > self.timeout]
        for k in expired:
            del self._buffers[k]
