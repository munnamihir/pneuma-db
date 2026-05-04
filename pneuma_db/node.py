"""
PNEUMA Node — Layer coordination + consistent hashing
======================================================
PNEUMANode ties together transport, crypto, framing,
and error correction into a single node object.

Consistent hashing distributes keys across nodes without
a central coordinator — same algorithm runs on every node,
so they all agree on which node owns which key.
"""

import hashlib
import json
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, List, Callable

from .crypto           import PNEUMACrypto, KeyPair, Session
from .transport        import PNEUMATransport
from .framing          import Framer, ReassemblyBuffer, Packet, Flags
from .error_correction import ErrorCorrection


# ── Consistent Hash Ring ─────────────────────────────────────
class HashRing:
    """
    Consistent hash ring for distributing keys across nodes.
    Adding/removing nodes only remaps ~1/N of keys.
    """

    def __init__(self, nodes: List[str], virtual_nodes: int = 150):
        self.virtual_nodes = virtual_nodes
        self._ring: dict[int, str] = {}
        self._sorted_keys: List[int] = []
        for node in nodes:
            self.add_node(node)

    def _hash(self, key: str) -> int:
        return int(hashlib.md5(key.encode()).hexdigest(), 16)

    def add_node(self, node_id: str):
        for i in range(self.virtual_nodes):
            h = self._hash(f"{node_id}:{i}")
            self._ring[h] = node_id
        self._sorted_keys = sorted(self._ring.keys())

    def remove_node(self, node_id: str):
        for i in range(self.virtual_nodes):
            h = self._hash(f"{node_id}:{i}")
            del self._ring[h]
        self._sorted_keys = sorted(self._ring.keys())

    def get_node(self, key: str) -> str:
        """Return the node responsible for this key."""
        if not self._ring:
            raise RuntimeError("Hash ring is empty")
        h = self._hash(key)
        for ring_key in self._sorted_keys:
            if h <= ring_key:
                return self._ring[ring_key]
        return self._ring[self._sorted_keys[0]]   # wrap around

    def get_nodes(self, key: str, n: int) -> List[str]:
        """Return n distinct nodes for replication."""
        if not self._ring:
            return []
        h     = self._hash(key)
        seen  = set()
        nodes = []
        idx   = 0
        # find start position
        for i, ring_key in enumerate(self._sorted_keys):
            if h <= ring_key:
                idx = i
                break

        total = len(self._sorted_keys)
        for i in range(total):
            node = self._ring[self._sorted_keys[(idx + i) % total]]
            if node not in seen:
                seen.add(node)
                nodes.append(node)
            if len(nodes) == n:
                break
        return nodes


# ── Node Status ──────────────────────────────────────────────
@dataclass
class NodeStatus:
    node_id:     str
    reachable:   bool
    latency_ms:  Optional[float] = None
    last_seen:   Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "node_id":    self.node_id,
            "reachable":  self.reachable,
            "latency_ms": self.latency_ms,
            "last_seen":  self.last_seen,
        }


# ── PNEUMA Node ──────────────────────────────────────────────
class PNEUMANode:
    """
    A single PNEUMA node. Coordinates all layers:
      - Transport (ultrasonic FSK)
      - Error correction (Reed-Solomon)
      - Framing (PPP packet protocol)
      - Crypto (ML-KEM + ChaCha20)
      - Routing (consistent hash ring)
    """

    def __init__(
        self,
        node_id:       str,
        known_nodes:   List[str],
        mlkem_level:   str   = "ML_KEM_768",
        replication:   int   = 3,
        symbol_ms:     int   = 100,
        freq_base:     int   = 17000,
        freq_step:     int   = 250,
        rs_parity:     int   = 16,
        session_ttl:   int   = 3600,
        sample_rate:   int   = 44100,
    ):
        self.node_id     = node_id
        self.replication = replication
        self.session_ttl = session_ttl

        # Crypto layer
        self.crypto   = PNEUMACrypto(mlkem_level)
        self.node_hash = self.crypto.node_id_hash(node_id)

        # Generate identity keypair (long-lived)
        self.identity_keypair: KeyPair = self.crypto.generate_keypair()

        # Session keypair (rotated per session)
        self.session_keypair: KeyPair = self.crypto.generate_keypair()

        # Active sessions: peer_id → Session
        self._sessions: dict[str, Session] = {}
        self._sessions_lock = threading.Lock()

        # Transport layer
        self.transport = PNEUMATransport(
            sample_rate = sample_rate,
            symbol_ms   = symbol_ms,
            freq_base   = freq_base,
            freq_step   = freq_step,
        )

        # Error correction layer
        self.ec = ErrorCorrection(parity=rs_parity)

        # Framing layer
        self.framer     = Framer(src_hash=self.node_hash)
        self.reassembly = ReassemblyBuffer(timeout=30.0)

        # Routing
        self.known_nodes = list(set(known_nodes))
        if node_id not in self.known_nodes:
            self.known_nodes.append(node_id)
        self.ring = HashRing(self.known_nodes)

        # Message handlers: op_code → callable
        self._handlers: dict[str, Callable] = {}

        # Peer status tracking
        self._peer_status: dict[str, NodeStatus] = {
            nid: NodeStatus(node_id=nid, reachable=(nid == node_id))
            for nid in self.known_nodes
        }

    # ── Routing ──────────────────────────────────────────────
    def owner_of(self, key: str) -> str:
        """Which node owns this key?"""
        return self.ring.get_node(key)

    def replicas_of(self, key: str) -> List[str]:
        """Which nodes hold replicas of this key?"""
        return self.ring.get_nodes(key, self.replication)

    def i_own(self, key: str) -> bool:
        """Does this node own the primary copy of this key?"""
        return self.owner_of(key) == self.node_id

    # ── Session management ───────────────────────────────────
    def get_or_create_session(self, peer_id: str, peer_public_key: bytes) -> Session:
        with self._sessions_lock:
            session = self._sessions.get(peer_id)
            if session and not session.is_expired():
                return session
            ciphertext, session = self.crypto.encapsulate(peer_public_key, peer_id)
            self._sessions[peer_id] = session
            return session

    def establish_session_from_ciphertext(self, peer_id: str, ciphertext: bytes) -> Session:
        with self._sessions_lock:
            session = self.crypto.decapsulate(
                self.session_keypair, ciphertext, peer_id
            )
            self._sessions[peer_id] = session
            return session

    def rotate_session_keypair(self):
        """Generate a fresh session keypair (call periodically)."""
        self.session_keypair = self.crypto.generate_keypair()

    # ── Packet send/receive ──────────────────────────────────
    def send_raw(self, payload: bytes, dst_hash: bytes, flags: Flags = Flags.ENCRYPTED):
        """Fragment, error-encode, and transmit payload."""
        ec_payload = self.ec.encode(payload)
        packets    = self.framer.fragment(ec_payload, dst_hash, flags)
        for pkt in packets:
            self.transport.transmit(pkt.serialize())

    def send_message(self, message: dict, peer_id: str, session: Optional[Session] = None):
        """
        Serialize, optionally encrypt, and send a message dict to a peer.
        """
        import json as _json
        raw      = _json.dumps(message).encode()
        dst_hash = self.crypto.node_id_hash(peer_id)

        if session:
            raw   = session.encrypt(raw)
            flags = Flags.ENCRYPTED
        else:
            flags = Flags.NONE

        self.send_raw(raw, dst_hash, flags)

    def receive_packet(self, raw: bytes) -> Optional[dict]:
        """
        Deserialize raw bytes → Packet → reassemble if fragmented
        → error-correct → decrypt → return message dict.
        Returns None if incomplete or invalid.
        """
        import json as _json

        pkt = Packet.deserialize(raw)
        if not pkt:
            return None

        payload = self.reassembly.add_packet(pkt)
        if payload is None:
            return None   # waiting for more fragments

        # Error correction
        corrected, n_errors = self.ec.decode_safe(payload)
        if corrected is None:
            return None   # uncorrectable error

        # Decrypt if flagged
        if pkt.flags & Flags.ENCRYPTED:
            peer_id = hashlib.sha256(pkt.src).hexdigest()[:16]
            session = self._sessions.get(peer_id)
            if session:
                try:
                    corrected = session.decrypt(corrected)
                except Exception:
                    return None

        try:
            return _json.loads(corrected)
        except Exception:
            return None   # not a JSON message

    # ── Node status ──────────────────────────────────────────
    def node_status(self) -> List[NodeStatus]:
        return list(self._peer_status.values())

    def mark_reachable(self, peer_id: str, latency_ms: float = None):
        if peer_id in self._peer_status:
            self._peer_status[peer_id].reachable  = True
            self._peer_status[peer_id].latency_ms = latency_ms
            self._peer_status[peer_id].last_seen  = time.time()

    def mark_unreachable(self, peer_id: str):
        if peer_id in self._peer_status:
            self._peer_status[peer_id].reachable = False

    def add_peer(self, peer_id: str):
        if peer_id not in self.known_nodes:
            self.known_nodes.append(peer_id)
            self.ring.add_node(peer_id)
            self._peer_status[peer_id] = NodeStatus(node_id=peer_id, reachable=False)

    def remove_peer(self, peer_id: str):
        if peer_id in self.known_nodes and peer_id != self.node_id:
            self.known_nodes.remove(peer_id)
            self.ring.remove_node(peer_id)

    # ── Loopback simulate (testing without audio hardware) ───
    def simulate_send_receive(self, message: dict, session: Optional[Session] = None) -> Optional[dict]:
        """
        Simulate a full send → receive cycle in-process.
        Useful for unit tests without audio hardware.
        """
        import json as _json
        raw      = _json.dumps(message).encode()
        dst_hash = self.node_hash

        if session:
            raw   = session.encrypt(raw)
            flags = Flags.ENCRYPTED
        else:
            flags = Flags.NONE

        ec_payload = self.ec.encode(raw)
        packets    = self.framer.fragment(ec_payload, dst_hash, flags)

        result = None
        for pkt in packets:
            serialized = pkt.serialize()
            # Simulate acoustic round-trip via loopback
            sim_bytes = self.transport.simulate_transmit_receive(serialized)
            result    = self.receive_packet(sim_bytes)

        return result
