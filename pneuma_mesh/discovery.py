"""
PNEUMA Node Discovery
=====================
Nodes announce themselves by periodically transmitting a
short ultrasonic BEACON packet containing:
  - node_id (string)
  - public_key (ML-KEM encapsulation key, hex)
  - known_peers (list of node IDs they already know)
  - timestamp

On receiving a beacon:
  1. Add the sender to the local node list
  2. Initiate ML-KEM key exchange
  3. Update the TDMA scheduler

No coordination needed — nodes self-organise.
"""

import json
import time
import hashlib
import threading
from typing import Callable, Optional

BEACON_INTERVAL_S = 3.0   # transmit a beacon every 3 seconds
BEACON_PREFIX     = b"\xBE\xAC\x01"  # 3-byte magic for beacon detection


class BeaconPayload:
    """A discovery beacon broadcast by a node to announce itself."""

    def __init__(
        self,
        node_id:     str,
        public_key:  bytes,
        known_peers: list[str],
        slot_ms:     int = 500,
    ):
        self.node_id     = node_id
        self.public_key  = public_key
        self.known_peers = known_peers
        self.slot_ms     = slot_ms
        self.timestamp   = time.time()

    def serialize(self) -> bytes:
        payload = json.dumps({
            "node_id":     self.node_id,
            "public_key":  self.public_key.hex(),
            "known_peers": self.known_peers,
            "slot_ms":     self.slot_ms,
            "ts":          self.timestamp,
        }).encode()
        return BEACON_PREFIX + len(payload).to_bytes(2, "big") + payload

    @staticmethod
    def deserialize(data: bytes) -> Optional["BeaconPayload"]:
        if not data.startswith(BEACON_PREFIX):
            return None
        try:
            length  = int.from_bytes(data[3:5], "big")
            payload = json.loads(data[5:5 + length])
            b = BeaconPayload.__new__(BeaconPayload)
            b.node_id     = payload["node_id"]
            b.public_key  = bytes.fromhex(payload["public_key"])
            b.known_peers = payload.get("known_peers", [])
            b.slot_ms     = payload.get("slot_ms", 500)
            b.timestamp   = payload.get("ts", 0)
            return b
        except Exception:
            return None

    def is_fresh(self, max_age_s: float = 30.0) -> bool:
        return (time.time() - self.timestamp) < max_age_s


class DiscoveryService:
    """
    Manages beacon transmission and reception for peer discovery.

    Usage:
        svc = DiscoveryService(node_id, public_key, transport, tdma)
        svc.on_peer_discovered = lambda beacon: print(f"Found {beacon.node_id}")
        svc.start()
    """

    def __init__(
        self,
        node_id:    str,
        public_key: bytes,
        transport,          # PNEUMATransport
        tdma,               # TDMAScheduler
        beacon_interval: float = BEACON_INTERVAL_S,
    ):
        self.node_id         = node_id
        self.public_key      = public_key
        self.transport       = transport
        self.tdma            = tdma
        self.beacon_interval = beacon_interval

        self.on_peer_discovered: Optional[Callable[[BeaconPayload], None]] = None
        self._known_peers:  list[str]         = [node_id]
        self._peer_keys:    dict[str, bytes]  = {}
        self._peer_seen:    dict[str, float]  = {}   # node_id → last seen ts
        self._lock          = threading.Lock()
        self._stop          = threading.Event()

    # ── Start / stop ─────────────────────────────────────────
    def start(self):
        self._stop.clear()
        threading.Thread(target=self._beacon_loop, daemon=True).start()
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    # ── Beacon transmission ───────────────────────────────────
    def _beacon_loop(self):
        """Transmit a discovery beacon every beacon_interval seconds."""
        last_beacon = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now - last_beacon >= self.beacon_interval:
                if self.tdma.is_my_turn() and not self.tdma.is_guard_period():
                    self._send_beacon()
                    last_beacon = now
            time.sleep(0.1)

    def _send_beacon(self):
        with self._lock:
            peers = list(self._known_peers)
        beacon = BeaconPayload(
            node_id     = self.node_id,
            public_key  = self.public_key,
            known_peers = peers,
            slot_ms     = self.tdma.slot_ms,
        )
        payload = beacon.serialize()
        try:
            self.transport.transmit(payload, include_preamble=True)
        except Exception as e:
            print(f"[Discovery] Beacon TX error: {e}")

    # ── Beacon reception ──────────────────────────────────────
    def _listen_loop(self):
        """Listen for beacons during other nodes' slots."""
        while not self._stop.is_set():
            if not self.tdma.is_my_turn():
                try:
                    beacon_size = 5 + 512   # prefix + len + max payload
                    data = self.transport.receive(
                        num_bytes          = beacon_size,
                        timeout            = self.tdma.slot_ms / 1000 + 0.5,
                        wait_for_preamble  = True,
                    )
                    if data:
                        beacon = BeaconPayload.deserialize(data)
                        if beacon and beacon.node_id != self.node_id:
                            self._handle_beacon(beacon)
                except Exception:
                    pass   # timeout or noise — normal
            else:
                time.sleep(0.05)

    def _handle_beacon(self, beacon: BeaconPayload):
        with self._lock:
            is_new = beacon.node_id not in self._known_peers
            self._peer_keys[beacon.node_id] = beacon.public_key
            self._peer_seen[beacon.node_id] = time.time()
            if is_new:
                self._known_peers.append(beacon.node_id)
                self.tdma.add_node(beacon.node_id)

        if is_new and self.on_peer_discovered:
            try:
                self.on_peer_discovered(beacon)
            except Exception as e:
                print(f"[Discovery] Callback error: {e}")

        # Also absorb peers-of-peers (transitive discovery)
        with self._lock:
            for peer_id in beacon.known_peers:
                if peer_id not in self._known_peers and peer_id != self.node_id:
                    self._known_peers.append(peer_id)
                    self.tdma.add_node(peer_id)

    # ── Queries ───────────────────────────────────────────────
    def known_peers(self) -> list[str]:
        with self._lock:
            return list(self._known_peers)

    def peer_public_key(self, node_id: str) -> Optional[bytes]:
        with self._lock:
            return self._peer_keys.get(node_id)

    def peer_last_seen(self, node_id: str) -> Optional[float]:
        with self._lock:
            return self._peer_seen.get(node_id)

    def active_peers(self, max_age_s: float = 30.0) -> list[str]:
        """Peers heard from within the last max_age_s seconds."""
        now = time.time()
        with self._lock:
            return [
                nid for nid, ts in self._peer_seen.items()
                if now - ts < max_age_s
            ]

    def prune_stale(self, max_age_s: float = 60.0):
        """Remove peers not heard from recently."""
        now = time.time()
        with self._lock:
            stale = [
                nid for nid, ts in self._peer_seen.items()
                if now - ts > max_age_s
            ]
            for nid in stale:
                self._known_peers = [n for n in self._known_peers if n != nid]
                self._peer_seen.pop(nid, None)
                self._peer_keys.pop(nid, None)
                self.tdma.remove_node(nid)
        return stale
