"""
PNEUMA Acoustic Mesh Node
=========================
A complete offline mesh node. Combines:
  - TDMA scheduling (whose turn to speak)
  - Peer discovery (beacon broadcasts)
  - ML-KEM key exchange (quantum-safe sessions)
  - FSK transport (ultrasonic physical layer)
  - Reed-Solomon error correction
  - PNEUMA-DB (distributed key-value store)

Everything runs over air. No internet. No WiFi. No Bluetooth.
No central coordinator. Any number of laptops in the same room.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import time
import threading
import hashlib
import queue
from typing import Optional, Callable, Any, List

from tdma       import TDMAScheduler
from discovery  import DiscoveryService, BeaconPayload

# Import PNEUMA-DB layers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pneuma-db"))
from pneuma_db.crypto           import PNEUMACrypto
from pneuma_db.transport        import PNEUMATransport
from pneuma_db.framing          import Framer, Packet, Flags, ReassemblyBuffer
from pneuma_db.error_correction import ErrorCorrection
from pneuma_db.node             import HashRing
from pneuma_db.db               import LocalStore, Table


class MeshMessage:
    """A message travelling through the acoustic mesh."""

    # Op codes
    PUT    = "PUT"
    GET    = "GET"
    REPLY  = "REPLY"
    ACK    = "ACK"
    DELETE = "DELETE"
    CAS    = "CAS"
    SCAN   = "SCAN"
    KEM    = "KEM"      # key exchange ciphertext

    def __init__(self, op: str, **kwargs):
        self.op         = op
        self.request_id = kwargs.get("request_id", "")
        self.sender     = kwargs.get("sender", "")
        self.key        = kwargs.get("key", "")
        self.value      = kwargs.get("value")
        self.expected   = kwargs.get("expected")
        self.ciphertext = kwargs.get("ciphertext", "")   # for KEM
        self.ok         = kwargs.get("ok")

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None and v != ""}

    @staticmethod
    def from_dict(d: dict) -> "MeshMessage":
        m = MeshMessage(d["op"])
        for k, v in d.items():
            setattr(m, k, v)
        return m

    def serialize(self) -> bytes:
        return json.dumps(self.to_dict()).encode()

    @staticmethod
    def deserialize(data: bytes) -> Optional["MeshMessage"]:
        try:
            return MeshMessage.from_dict(json.loads(data))
        except Exception:
            return None


class AcousticMeshNode:
    """
    A self-contained PNEUMA acoustic mesh node.
    Drop this on any laptop and it joins the room mesh automatically.

    Quick start:
        node = AcousticMeshNode("laptop-a")
        node.start()

        node.db_put("config:version", "1.0")
        print(node.db_get("config:version"))
    """

    def __init__(
        self,
        node_id:     str,
        slot_ms:     int   = 500,
        symbol_ms:   int   = 80,
        replication: int   = 2,
        db_path:     Optional[str] = None,
    ):
        self.node_id     = node_id
        self.replication = replication
        self._lock       = threading.Lock()
        self._stop       = threading.Event()

        print(f"[{node_id}] Initialising PNEUMA acoustic mesh node")

        # ── Crypto ────────────────────────────────────────────
        self.crypto   = PNEUMACrypto()
        self.node_hash = self.crypto.node_id_hash(node_id)
        self.keypair  = self.crypto.generate_keypair()
        self._sessions: dict[str, object] = {}   # peer_id → Session
        print(f"[{node_id}] ML-KEM keypair generated ({self.keypair.algorithm})")

        # ── Transport ─────────────────────────────────────────
        self.transport = PNEUMATransport(symbol_ms=symbol_ms)
        self.ec        = ErrorCorrection(parity=16)
        self.framer    = Framer(src_hash=self.node_hash)
        self.reassembly = ReassemblyBuffer(timeout=30.0)

        # ── TDMA ──────────────────────────────────────────────
        self.tdma = TDMAScheduler(
            node_id   = node_id,
            all_nodes = [node_id],
            slot_ms   = slot_ms,
        )

        # ── Discovery ─────────────────────────────────────────
        self.discovery = DiscoveryService(
            node_id    = node_id,
            public_key = self.keypair.public_key,
            transport  = self.transport,
            tdma       = self.tdma,
        )
        self.discovery.on_peer_discovered = self._on_peer_discovered

        # ── Routing ───────────────────────────────────────────
        self.ring = HashRing([node_id])

        # ── Storage ───────────────────────────────────────────
        self.store = LocalStore(node_id, db_path)

        # ── Message handling ──────────────────────────────────
        self._pending: dict[str, threading.Event] = {}
        self._replies: dict[str, Any]             = {}
        self._outbox:  queue.Queue                = queue.Queue(maxsize=64)

        print(f"[{node_id}] Ready — TDMA slot {self.tdma.my_slot}, {slot_ms}ms/slot")

    # ── Lifecycle ─────────────────────────────────────────────
    def start(self):
        """Start all background threads."""
        self._stop.clear()
        self.discovery.start()
        threading.Thread(target=self._tx_loop,  daemon=True, name=f"tx-{self.node_id}").start()
        threading.Thread(target=self._rx_loop,  daemon=True, name=f"rx-{self.node_id}").start()
        threading.Thread(target=self._prune_loop, daemon=True, name=f"prune-{self.node_id}").start()
        print(f"[{self.node_id}] Acoustic mesh started — listening for peers")

    def stop(self):
        self._stop.set()
        self.discovery.stop()

    # ── Peer discovery callback ───────────────────────────────
    def _on_peer_discovered(self, beacon: BeaconPayload):
        peer_id = beacon.node_id
        print(f"[{self.node_id}] Peer discovered: {peer_id}")
        self.ring.add_node(peer_id)
        # Initiate ML-KEM key exchange
        self._initiate_kem(peer_id, beacon.public_key)

    def _initiate_kem(self, peer_id: str, peer_public_key: bytes):
        """Sender side: encapsulate → send ciphertext to peer."""
        try:
            ciphertext, session = self.crypto.encapsulate(peer_public_key, peer_id)
            with self._lock:
                self._sessions[peer_id] = session
            # Send ciphertext so peer can derive the same session key
            msg = MeshMessage(
                op         = MeshMessage.KEM,
                sender     = self.node_id,
                ciphertext = ciphertext.hex(),
            )
            self._queue_to_peer(peer_id, msg, encrypt=False)
            print(f"[{self.node_id}] ML-KEM session initiated with {peer_id}")
        except Exception as e:
            print(f"[{self.node_id}] KEM init error with {peer_id}: {e}")

    def _handle_kem(self, msg: MeshMessage):
        """Receiver side: decapsulate ciphertext → derive session key."""
        peer_id    = msg.sender
        ciphertext = bytes.fromhex(msg.ciphertext)
        try:
            session = self.crypto.decapsulate(self.keypair, ciphertext, peer_id)
            with self._lock:
                self._sessions[peer_id] = session
            print(f"[{self.node_id}] ML-KEM session established with {peer_id}")
        except Exception as e:
            print(f"[{self.node_id}] KEM decaps error from {peer_id}: {e}")

    # ── Transmit loop ─────────────────────────────────────────
    def _tx_loop(self):
        """Wait for our TDMA slot, then transmit queued packets."""
        while not self._stop.is_set():
            if self.tdma.is_my_turn() and not self.tdma.is_guard_period():
                # Drain outbox — send as many packets as fit in the slot
                deadline = time.time() + (self.tdma.slot_ms - self.tdma.guard_ms) / 1000
                while time.time() < deadline:
                    try:
                        raw_packet = self._outbox.get_nowait()
                        self.transport.transmit(raw_packet, include_preamble=True)
                    except queue.Empty:
                        break
                    except Exception as e:
                        print(f"[{self.node_id}] TX error: {e}")
            time.sleep(0.02)

    # ── Receive loop ──────────────────────────────────────────
    def _rx_loop(self):
        """Listen during other nodes' slots and process incoming messages."""
        while not self._stop.is_set():
            if not self.tdma.is_my_turn():
                owner = self.tdma.current_owner()
                try:
                    # Estimate bytes to receive: max packet size
                    max_bytes = 280   # PNEUMA max packet
                    raw = self.transport.receive(
                        num_bytes         = max_bytes,
                        timeout           = self.tdma.slot_ms / 1000 + 0.2,
                        wait_for_preamble = True,
                    )
                    if raw:
                        self._process_raw(raw)
                except Exception:
                    pass   # timeout or noise — completely normal
            else:
                time.sleep(0.05)

    def _process_raw(self, raw: bytes):
        """Deserialise raw bytes → packet → error correct → decrypt → handle."""
        # First check if it's a beacon
        from discovery import BEACON_PREFIX
        if raw.startswith(BEACON_PREFIX):
            beacon = BeaconPayload.deserialize(raw)
            if beacon and beacon.node_id != self.node_id:
                self.discovery._handle_beacon(beacon)
            return

        # Try as a PNEUMA packet
        pkt = Packet.deserialize(raw)
        if not pkt:
            return

        payload = self.reassembly.add_packet(pkt)
        if payload is None:
            return   # waiting for more fragments

        corrected, _ = self.ec.decode_safe(payload)
        if corrected is None:
            return

        # Decrypt if encrypted
        if pkt.flags & Flags.ENCRYPTED:
            src_id  = self._hash_to_node_id(pkt.src)
            session = self._sessions.get(src_id)
            if not session:
                return
            try:
                corrected = session.decrypt(corrected)
            except Exception:
                return

        msg = MeshMessage.deserialize(corrected)
        if msg:
            self._handle_message(msg)

    def _hash_to_node_id(self, node_hash: bytes) -> str:
        """Reverse-lookup node_id from its 8-byte hash."""
        for nid in self.discovery.known_peers():
            if self.crypto.node_id_hash(nid) == node_hash:
                return nid
        return ""

    # ── Message handling ──────────────────────────────────────
    def _handle_message(self, msg: MeshMessage):
        op = msg.op
        if op == MeshMessage.KEM:
            self._handle_kem(msg)
        elif op == MeshMessage.PUT:
            self.store.put(msg.key, msg.value)
            self._send_ack(msg.sender, msg.request_id, True)
        elif op == MeshMessage.GET:
            value = self.store.get(msg.key)
            self._send_reply(msg.sender, msg.request_id, value)
        elif op == MeshMessage.DELETE:
            self.store.delete(msg.key)
            self._send_ack(msg.sender, msg.request_id, True)
        elif op == MeshMessage.CAS:
            ok = self.store.cas(msg.key, msg.expected, msg.value)
            self._send_ack(msg.sender, msg.request_id, ok)
        elif op in (MeshMessage.REPLY, MeshMessage.ACK):
            ev = self._pending.pop(msg.request_id, None)
            if ev:
                self._replies[msg.request_id] = msg.value if op == MeshMessage.REPLY else msg.ok
                ev.set()

    def _send_ack(self, peer_id: str, request_id: str, ok: bool):
        msg = MeshMessage(op=MeshMessage.ACK, sender=self.node_id,
                          request_id=request_id, ok=ok)
        self._queue_to_peer(peer_id, msg)

    def _send_reply(self, peer_id: str, request_id: str, value: Any):
        msg = MeshMessage(op=MeshMessage.REPLY, sender=self.node_id,
                          request_id=request_id, value=value)
        self._queue_to_peer(peer_id, msg)

    # ── Packet queueing ───────────────────────────────────────
    def _queue_to_peer(self, peer_id: str, msg: MeshMessage, encrypt: bool = True):
        """Serialise, optionally encrypt, fragment and queue a message."""
        raw     = msg.serialize()
        flags   = Flags.NONE

        if encrypt:
            session = self._sessions.get(peer_id)
            if session:
                raw   = session.encrypt(raw)
                flags = Flags.ENCRYPTED

        ec_raw   = self.ec.encode(raw)
        dst_hash = self.crypto.node_id_hash(peer_id)
        packets  = self.framer.fragment(ec_raw, dst_hash, flags)

        for pkt in packets:
            try:
                self._outbox.put_nowait(pkt.serialize())
            except queue.Full:
                print(f"[{self.node_id}] Outbox full — packet dropped")

    # ── Remote call helper ────────────────────────────────────
    def _remote_call(self, peer_id: str, msg: MeshMessage, timeout: float = 8.0) -> Any:
        """Send a message and wait for a reply/ack."""
        import uuid
        req_id       = str(uuid.uuid4())[:8]
        msg.request_id = req_id
        msg.sender   = self.node_id
        ev           = threading.Event()
        self._pending[req_id] = ev
        self._queue_to_peer(peer_id, msg)
        ev.wait(timeout=timeout)
        return self._replies.pop(req_id, None)

    # ── Public DB API ─────────────────────────────────────────
    def db_put(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        owner = self.ring.get_node(key)
        if owner == self.node_id:
            ok = self.store.put(key, value, ttl)
            # Replicate
            for replica in self._replica_peers(key):
                msg = MeshMessage(op=MeshMessage.PUT, key=key, value=value)
                self._queue_to_peer(replica, msg)
            return ok
        else:
            msg = MeshMessage(op=MeshMessage.PUT, key=key, value=value)
            result = self._remote_call(owner, msg)
            return bool(result)

    def db_get(self, key: str) -> Optional[Any]:
        local = self.store.get(key)
        if local is not None:
            return local
        owner = self.ring.get_node(key)
        if owner == self.node_id:
            return None
        msg = MeshMessage(op=MeshMessage.GET, key=key)
        return self._remote_call(owner, msg)

    def db_delete(self, key: str) -> bool:
        self.store.delete(key)
        owner = self.ring.get_node(key)
        if owner != self.node_id:
            msg = MeshMessage(op=MeshMessage.DELETE, key=key)
            self._remote_call(owner, msg)
        return True

    def db_cas(self, key: str, expected: Any, new_value: Any) -> bool:
        owner = self.ring.get_node(key)
        if owner == self.node_id:
            return self.store.cas(key, expected, new_value)
        msg = MeshMessage(op=MeshMessage.CAS, key=key,
                          expected=expected, value=new_value)
        return bool(self._remote_call(owner, msg))

    def db_scan(self, prefix: str) -> dict:
        return self.store.scan_prefix(prefix)

    def table(self, name: str) -> Table:
        from pneuma_db.db import PNEUMA_DB
        class _FakeDB:
            def __init__(self, node):
                self._node = node
            def put(self, k, v, ttl=None): return node.db_put(k, v, ttl)
            def get(self, k): return node.db_get(k)
            def delete(self, k): return node.db_delete(k)
            def cas(self, k, e, n): return node.db_cas(k, e, n)
        node = self
        return Table(_FakeDB(self), name)

    def _replica_peers(self, key: str) -> List[str]:
        peers   = self.discovery.active_peers()
        replicas = self.ring.get_nodes(key, self.replication)
        return [n for n in replicas if n != self.node_id and n in peers]

    # ── Prune stale peers ─────────────────────────────────────
    def _prune_loop(self):
        while not self._stop.is_set():
            time.sleep(30)
            stale = self.discovery.prune_stale(max_age_s=60)
            for nid in stale:
                self.ring.remove_node(nid)
                with self._lock:
                    self._sessions.pop(nid, None)
                print(f"[{self.node_id}] Pruned stale peer: {nid}")

    # ── Status ────────────────────────────────────────────────
    def status(self) -> dict:
        return {
            "node_id":       self.node_id,
            "algorithm":     self.keypair.algorithm,
            "tdma_slot":     f"{self.tdma.my_slot}/{self.tdma.num_nodes}",
            "slot_ms":       self.tdma.slot_ms,
            "peers":         self.discovery.active_peers(),
            "sessions":      list(self._sessions.keys()),
            "local_records": self.store.count(),
            "outbox_depth":  self._outbox.qsize(),
        }

    def __repr__(self):
        return f"AcousticMeshNode({self.node_id}, peers={self.discovery.active_peers()})"
