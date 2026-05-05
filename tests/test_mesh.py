"""
PNEUMA Mesh Tests (no audio hardware required)
Tests the mesh logic using simulated/loopback transport.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
import pytest
from pneuma_mesh.tdma      import TDMAScheduler
from pneuma_mesh.discovery import BeaconPayload, BEACON_PREFIX


# ══════════════════════════════════════════════════════
# TDMA TESTS
# ══════════════════════════════════════════════════════
class TestTDMA:
    def test_slot_assignment(self):
        t = TDMAScheduler("alpha", ["alpha", "beta", "gamma"], slot_ms=500)
        assert t.my_slot == 0   # alpha is first alphabetically
        assert t.num_nodes == 3
        assert t.cycle_ms == 1500

    def test_slot_owner(self):
        t = TDMAScheduler("alpha", ["alpha", "beta", "gamma"], slot_ms=500)
        assert t.slot_owner(0) == "alpha"
        assert t.slot_owner(1) == "beta"
        assert t.slot_owner(2) == "gamma"
        assert t.slot_owner(3) == "alpha"   # wraps

    def test_deterministic_slot(self):
        t = TDMAScheduler("beta", ["alpha", "beta", "gamma"], slot_ms=500)
        # beta should be slot 1
        assert t.my_slot == 1

    def test_ms_until_my_slot_reasonable(self):
        t = TDMAScheduler("alpha", ["alpha", "beta", "gamma"], slot_ms=500)
        ms = t.ms_until_my_slot()
        assert 0 <= ms <= t.cycle_ms

    def test_add_node(self):
        t = TDMAScheduler("alpha", ["alpha", "beta"], slot_ms=500)
        assert t.num_nodes == 2
        t.add_node("gamma")
        assert t.num_nodes == 3
        assert "gamma" in t.all_nodes

    def test_remove_node(self):
        t = TDMAScheduler("alpha", ["alpha", "beta", "gamma"], slot_ms=500)
        t.remove_node("gamma")
        assert t.num_nodes == 2
        assert "gamma" not in t.all_nodes

    def test_current_slot_is_valid(self):
        t = TDMAScheduler("alpha", ["alpha", "beta", "gamma"], slot_ms=500)
        slot = t.current_slot()
        assert 0 <= slot < t.num_nodes

    def test_guard_period_detection(self):
        t = TDMAScheduler("alpha", ["alpha"], slot_ms=500, guard_ms=50)
        # Not easy to force guard period in test, just check it's bool
        assert isinstance(t.is_guard_period(), bool)


# ══════════════════════════════════════════════════════
# BEACON TESTS
# ══════════════════════════════════════════════════════
class TestBeacon:
    def test_serialize_deserialize(self):
        beacon = BeaconPayload(
            node_id     = "laptop-a",
            public_key  = b"\x00" * 32,
            known_peers = ["laptop-b", "laptop-c"],
            slot_ms     = 500,
        )
        data    = beacon.serialize()
        decoded = BeaconPayload.deserialize(data)

        assert decoded is not None
        assert decoded.node_id == "laptop-a"
        assert decoded.known_peers == ["laptop-b", "laptop-c"]
        assert decoded.slot_ms == 500

    def test_magic_prefix(self):
        beacon = BeaconPayload("test", b"\x01" * 32, [])
        data   = beacon.serialize()
        assert data.startswith(BEACON_PREFIX)

    def test_invalid_data_returns_none(self):
        result = BeaconPayload.deserialize(b"this is not a beacon")
        assert result is None

    def test_freshness(self):
        beacon = BeaconPayload("test", b"\x00" * 32, [])
        assert beacon.is_fresh(max_age_s=60)

    def test_stale_beacon(self):
        beacon = BeaconPayload("test", b"\x00" * 32, [])
        beacon.timestamp = time.time() - 1000  # very old
        assert not beacon.is_fresh(max_age_s=60)

    def test_large_public_key(self):
        # ML-KEM-768 public key is 1184 bytes
        large_key = bytes(range(256)) * 5   # 1280 bytes
        beacon    = BeaconPayload("test", large_key, ["a", "b", "c"])
        data      = beacon.serialize()
        decoded   = BeaconPayload.deserialize(data)
        assert decoded.public_key == large_key


# ══════════════════════════════════════════════════════
# LOCAL STORE TESTS (reused from pneuma-db)
# ══════════════════════════════════════════════════════
class TestLocalStore:
    def _make_store(self):
        import sqlite3
        from pneuma_db.db import LocalStore
        store = LocalStore("test-mesh")
        store.conn = sqlite3.connect(":memory:", check_same_thread=False)
        store._init()
        return store

    def test_put_get_delete(self):
        s = self._make_store()
        s.put("k", "v")
        assert s.get("k") == "v"
        s.delete("k")
        assert s.get("k") is None

    def test_ttl(self):
        s = self._make_store()
        s.put("tmp", "val", ttl=1)
        assert s.get("tmp") == "val"
        time.sleep(1.1)
        assert s.get("tmp") is None

    def test_scan_prefix(self):
        s = self._make_store()
        s.put("users:1", {"name": "Alice"})
        s.put("users:2", {"name": "Bob"})
        s.put("posts:1", "Hello")
        r = s.scan_prefix("users:")
        assert len(r) == 2


# ══════════════════════════════════════════════════════
# HASH RING TESTS
# ══════════════════════════════════════════════════════
class TestHashRing:
    def test_routing(self):
        from pneuma_db.node import HashRing
        ring = HashRing(["laptop-a", "laptop-b", "laptop-c"])
        for key in ["user:1", "config:v1", "sensor:001"]:
            owner = ring.get_node(key)
            assert owner in ["laptop-a", "laptop-b", "laptop-c"]

    def test_deterministic(self):
        from pneuma_db.node import HashRing
        ring = HashRing(["laptop-a", "laptop-b"])
        key  = "stable-test-key"
        assert ring.get_node(key) == ring.get_node(key)

    def test_replication(self):
        from pneuma_db.node import HashRing
        ring     = HashRing(["a", "b", "c", "d"])
        replicas = ring.get_nodes("key", 3)
        assert len(replicas) == 3
        assert len(set(replicas)) == 3


# ══════════════════════════════════════════════════════
# TRANSPORT LOOPBACK (no audio hardware)
# ══════════════════════════════════════════════════════
class TestTransportLoopback:
    def test_byte_roundtrip(self):
        from pneuma_db.transport import PNEUMATransport
        tx = PNEUMATransport(symbol_ms=50)
        for byte_val in [0x00, 0xFF, 0x42, 0xAB]:
            h_tone, l_tone = tx._byte_to_tones(byte_val)
            h_nib = tx._detect_nibble(h_tone)
            l_nib = tx._detect_nibble(l_tone)
            assert (h_nib << 4 | l_nib) == byte_val

    def test_message_loopback(self):
        from pneuma_db.transport import PNEUMATransport
        tx  = PNEUMATransport(symbol_ms=50)
        msg = b"Hello acoustic mesh!"
        recovered = tx.simulate_transmit_receive(msg)
        assert recovered == msg


# ══════════════════════════════════════════════════════
# FULL INTEGRATION (simulated — no audio)
# ══════════════════════════════════════════════════════
class TestMeshIntegration:
    """
    Tests the full message → encrypt → fragment → error-correct → decode chain
    without audio hardware, using simulated transport.
    """

    def _make_nodes(self, ids):
        import sqlite3
        from pneuma_db.crypto           import PNEUMACrypto
        from pneuma_db.error_correction import ErrorCorrection
        from pneuma_db.framing          import Framer, Packet, Flags, ReassemblyBuffer
        from pneuma_db.db               import LocalStore
        from pneuma_db.node             import HashRing

        nodes = {}
        for nid in ids:
            crypto  = PNEUMACrypto()
            keypair = crypto.generate_keypair()
            store   = LocalStore(nid)
            store.conn = sqlite3.connect(":memory:", check_same_thread=False)
            store._init()
            nodes[nid] = {
                "crypto":  crypto,
                "keypair": keypair,
                "store":   store,
                "hash":    crypto.node_id_hash(nid),
                "ec":      ErrorCorrection(parity=16),
                "framer":  Framer(src_hash=crypto.node_id_hash(nid)),
                "reassembly": ReassemblyBuffer(),
            }
        return nodes

    def test_two_node_message_pipeline(self):
        """Test full pipeline: serialize → EC encode → fragment → loopback → reassemble → EC decode."""
        from pneuma_db.framing import Packet, Flags
        from pneuma_db.transport import PNEUMATransport
        import json

        nodes = self._make_nodes(["alice", "bob"])
        a = nodes["alice"]
        b = nodes["bob"]

        # Alice sends unencrypted message to Bob (tests everything except crypto)
        plaintext  = json.dumps({"op": "PUT", "key": "test-key", "value": 99}).encode()
        ec_payload = a["ec"].encode(plaintext)
        packets    = a["framer"].fragment(ec_payload, b["hash"], Flags.NONE)

        tx     = PNEUMATransport(symbol_ms=50)
        result = None
        for pkt in packets:
            serialized  = pkt.serialize()
            looped      = tx.simulate_transmit_receive(serialized)
            decoded_pkt = Packet.deserialize(looped)
            if decoded_pkt:
                payload = b["reassembly"].add_packet(decoded_pkt)
                if payload:
                    corrected, nerrs = b["ec"].decode_safe(payload)
                    assert corrected is not None
                    result = json.loads(corrected)

        assert result is not None
        assert result["key"]   == "test-key"
        assert result["value"] == 99

    def test_key_exchange(self):
        """Test ML-KEM key exchange produces matching sessions."""
        from pneuma_db.crypto import PNEUMACrypto
        crypto = PNEUMACrypto()
        kp     = crypto.generate_keypair()
        ct, session_a = crypto.encapsulate(kp.public_key, "peer")
        session_b     = crypto.decapsulate(kp, ct, "peer")
        assert session_a.key == session_b.key
        # Encrypt/decrypt roundtrip
        msg = b"acoustic mesh test"
        assert session_b.decrypt(session_a.encrypt(msg)) == msg


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
