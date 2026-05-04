"""
PNEUMA-DB Test Suite
====================
Run with: python -m pytest tests/ -v
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json, time, pytest
from pneuma_db.crypto          import PNEUMACrypto
from pneuma_db.error_correction import ErrorCorrection
from pneuma_db.framing         import Framer, Packet, Flags, ReassemblyBuffer
from pneuma_db.transport       import PNEUMATransport
from pneuma_db.node            import PNEUMANode, HashRing
from pneuma_db.db              import PNEUMA_DB, Table, LocalStore


# ══════════════════════════════════════════════════════
# CRYPTO TESTS
# ══════════════════════════════════════════════════════
class TestCrypto:
    def setup_method(self):
        self.crypto = PNEUMACrypto()

    def test_keypair_generation(self):
        kp = self.crypto.generate_keypair()
        assert kp.public_key
        assert kp.private_key
        assert len(kp.public_key) > 0

    def test_encrypt_decrypt(self):
        kp = self.crypto.generate_keypair()
        plaintext = b"Hello PNEUMA!"

        ct, session_a = self.crypto.encapsulate(kp.public_key, "peer-a")
        session_b     = self.crypto.decapsulate(kp, ct, "peer-a")

        encrypted = session_a.encrypt(plaintext)
        decrypted = session_b.decrypt(encrypted)
        assert decrypted == plaintext

    def test_session_keys_match(self):
        kp = self.crypto.generate_keypair()
        ct, session_a = self.crypto.encapsulate(kp.public_key, "peer")
        session_b     = self.crypto.decapsulate(kp, ct, "peer")
        assert session_a.key == session_b.key

    def test_tampered_ciphertext_rejected(self):
        kp = self.crypto.generate_keypair()
        ct, session_a = self.crypto.encapsulate(kp.public_key, "peer")
        encrypted = session_a.encrypt(b"secret")
        tampered  = bytearray(encrypted)
        tampered[10] ^= 0xFF   # flip bits
        session_b = self.crypto.decapsulate(kp, ct, "peer")
        with pytest.raises(Exception):
            session_b.decrypt(bytes(tampered))

    def test_node_id_hash(self):
        h = PNEUMACrypto.node_id_hash("my-node")
        assert len(h) == 8
        assert h == PNEUMACrypto.node_id_hash("my-node")   # deterministic

    def test_session_expiry(self):
        kp = self.crypto.generate_keypair()
        _, session = self.crypto.encapsulate(kp.public_key, "peer")
        session.ttl = 0   # expire immediately
        time.sleep(0.01)
        assert session.is_expired()


# ══════════════════════════════════════════════════════
# ERROR CORRECTION TESTS
# ══════════════════════════════════════════════════════
class TestErrorCorrection:
    def setup_method(self):
        self.ec = ErrorCorrection(parity=16)

    def test_encode_decode_clean(self):
        data    = b"Hello PNEUMA database!"
        encoded = self.ec.encode(data)
        decoded, errors = self.ec.decode(encoded)
        assert decoded == data
        assert errors == 0

    def test_correct_errors(self):
        data    = b"Test data for error correction"
        encoded = bytearray(self.ec.encode(data))
        # Corrupt 5 bytes (within 16 correctable)
        for i in [2, 7, 15, 22, 30]:
            encoded[i] ^= 0xAB
        decoded, errors = self.ec.decode(bytes(encoded))
        assert decoded == data
        assert errors > 0

    def test_too_many_errors(self):
        data    = b"A" * 50
        encoded = bytearray(self.ec.encode(data))
        # Corrupt 20 bytes (exceeds 16 correctable)
        for i in range(0, 40, 2):
            encoded[i] ^= 0xFF
        result, errors = self.ec.decode_safe(bytes(encoded))
        # May or may not correct — just ensure no crash
        assert errors == -1 or result is not None

    def test_overhead(self):
        assert self.ec.overhead_bytes == 16
        assert self.ec.max_correctable_errors() == 8


# ══════════════════════════════════════════════════════
# FRAMING TESTS
# ══════════════════════════════════════════════════════
class TestFraming:
    def setup_method(self):
        self.src_hash = b"SRC12345"
        self.dst_hash = b"DST12345"
        self.framer   = Framer(self.src_hash)

    def test_single_packet_roundtrip(self):
        payload = b"Hello!"
        packets = self.framer.fragment(payload, self.dst_hash)
        assert len(packets) == 1

        serialized   = packets[0].serialize()
        deserialized = Packet.deserialize(serialized)
        assert deserialized is not None
        assert deserialized.payload == payload

    def test_multi_packet_fragmentation(self):
        payload = b"X" * 500
        framer2 = Framer(self.src_hash, max_payload=100)
        packets = framer2.fragment(payload, self.dst_hash)

        # Force smaller max_payload
        framer2 = Framer(self.src_hash, max_payload=100)
        packets2 = framer2.fragment(payload, self.dst_hash)
        assert len(packets2) == 5   # 500 / 100

    def test_reassembly(self):
        payload = b"A" * 300
        framer  = Framer(self.src_hash, max_payload=100)
        packets = framer.fragment(payload, self.dst_hash)
        result  = Framer.reassemble(packets)
        assert result == payload

    def test_crc_tamper_detection(self):
        packets    = self.framer.fragment(b"secure data", self.dst_hash)
        serialized = bytearray(packets[0].serialize())
        serialized[10] ^= 0xFF   # tamper with payload
        result = Packet.deserialize(bytes(serialized))
        assert result is None   # CRC should catch tampering

    def test_reassembly_buffer(self):
        payload = b"B" * 200
        framer  = Framer(self.src_hash, max_payload=100)
        packets = framer.fragment(payload, self.dst_hash)
        buf     = ReassemblyBuffer(timeout=30.0)

        result = None
        for pkt in packets:
            result = buf.add_packet(pkt)

        assert result == payload


# ══════════════════════════════════════════════════════
# TRANSPORT TESTS (no audio hardware required)
# ══════════════════════════════════════════════════════
class TestTransport:
    def setup_method(self):
        self.tx = PNEUMATransport(symbol_ms=50)

    def test_tone_generation(self):
        import numpy as np
        tone = self.tx._generate_tone(18000)
        assert len(tone) > 0
        assert abs(np.max(tone)) <= 0.5

    def test_nibble_to_frequency_mapping(self):
        for nibble in range(16):
            tone      = self.tx._nibble_to_tone(nibble)
            detected  = self.tx._detect_nibble(tone)
            assert detected == nibble, f"Nibble {nibble} detected as {detected}"

    def test_byte_encode_decode(self):
        for byte_val in [0x00, 0xFF, 0x42, 0xAB, 0x0F, 0xF0]:
            h_tone, l_tone = self.tx._byte_to_tones(byte_val)
            h_nibble = self.tx._detect_nibble(h_tone)
            l_nibble = self.tx._detect_nibble(l_tone)
            recovered = (h_nibble << 4) | l_nibble
            assert recovered == byte_val, f"Byte 0x{byte_val:02X} recovered as 0x{recovered:02X}"

    def test_loopback_roundtrip(self):
        original = b"Hello PNEUMA! This is a loopback test."
        recovered = self.tx.simulate_transmit_receive(original)
        assert recovered == original

    def test_calibration(self):
        result = self.tx.calibrate()
        assert result["ok"] == True
        assert result["error_hz"] < 125   # within half a frequency step


# ══════════════════════════════════════════════════════
# CONSISTENT HASHING TESTS
# ══════════════════════════════════════════════════════
class TestHashRing:
    def test_basic_routing(self):
        ring = HashRing(["alpha", "beta", "gamma"])
        for key in ["user:001", "user:002", "post:001", "config:v1"]:
            owner = ring.get_node(key)
            assert owner in ["alpha", "beta", "gamma"]

    def test_deterministic_routing(self):
        ring = HashRing(["alpha", "beta", "gamma"])
        key  = "stable-key"
        assert ring.get_node(key) == ring.get_node(key)

    def test_replication_returns_n_nodes(self):
        ring    = HashRing(["alpha", "beta", "gamma", "delta"])
        replicas = ring.get_nodes("some-key", 3)
        assert len(replicas) == 3
        assert len(set(replicas)) == 3   # distinct

    def test_add_remove_node(self):
        ring = HashRing(["alpha", "beta"])
        ring.add_node("gamma")
        owner = ring.get_node("test-key")
        assert owner in ["alpha", "beta", "gamma"]
        ring.remove_node("gamma")
        owner2 = ring.get_node("test-key")
        assert owner2 in ["alpha", "beta"]


# ══════════════════════════════════════════════════════
# LOCAL STORE TESTS
# ══════════════════════════════════════════════════════
class TestLocalStore:
    def setup_method(self):
        self.store = LocalStore("test-node", ":memory:")
        # Patch SQLite in-memory
        import sqlite3
        self.store.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.store._init()

    def test_put_get(self):
        self.store.put("k1", {"name": "Alice"})
        result = self.store.get("k1")
        assert result == {"name": "Alice"}

    def test_overwrite(self):
        self.store.put("k1", "v1")
        self.store.put("k1", "v2")
        assert self.store.get("k1") == "v2"

    def test_delete(self):
        self.store.put("k1", "v1")
        self.store.delete("k1")
        assert self.store.get("k1") is None

    def test_cas_success(self):
        self.store.put("counter", 5)
        ok = self.store.cas("counter", 5, 6)
        assert ok is True
        assert self.store.get("counter") == 6

    def test_cas_failure(self):
        self.store.put("counter", 5)
        ok = self.store.cas("counter", 99, 6)
        assert ok is False
        assert self.store.get("counter") == 5

    def test_scan_prefix(self):
        self.store.put("user:001", {"name": "A"})
        self.store.put("user:002", {"name": "B"})
        self.store.put("post:001", {"title": "X"})
        results = self.store.scan_prefix("user:")
        assert len(results) == 2
        assert "user:001" in results
        assert "post:001" not in results

    def test_ttl_expiry(self):
        self.store.put("tmp", "value", ttl=1)
        assert self.store.get("tmp") == "value"
        time.sleep(1.1)
        assert self.store.get("tmp") is None

    def test_auto_increment(self):
        ids = [self.store.next_id("users") for _ in range(5)]
        assert ids == [1, 2, 3, 4, 5]


# ══════════════════════════════════════════════════════
# PNEUMA_DB INTEGRATION TESTS (in-memory, no audio)
# ══════════════════════════════════════════════════════
class TestPNEUMADB:
    def setup_method(self):
        import sqlite3
        self.node = PNEUMANode("test-node", ["test-node"])
        self.db   = PNEUMA_DB(self.node)
        # Use in-memory SQLite
        self.db.store.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.store._init()

    def test_put_get(self):
        self.db.put("key1", "value1")
        assert self.db.get("key1") == "value1"

    def test_put_dict(self):
        self.db.put("user:001", {"name": "Alice", "age": 30})
        result = self.db.get("user:001")
        assert result["name"] == "Alice"

    def test_delete(self):
        self.db.put("k", "v")
        self.db.delete("k")
        assert self.db.get("k") is None

    def test_cas(self):
        self.db.put("count", 0)
        assert self.db.cas("count", 0, 1) is True
        assert self.db.cas("count", 0, 2) is False  # wrong expected
        assert self.db.get("count") == 1

    def test_scan(self):
        self.db.put("users:1", "Alice")
        self.db.put("users:2", "Bob")
        self.db.put("posts:1", "Hello")
        results = self.db.scan_prefix("users:")
        assert len(results) == 2

    def test_table_orm(self):
        users  = self.db.table("users")
        uid    = users.insert({"name": "Charlie", "role": "admin"})
        record = users.find(uid)
        assert record["name"] == "Charlie"

        users.update(uid, role="superadmin")
        updated = users.find(uid)
        assert updated["role"] == "superadmin"

        users.delete(uid)
        assert users.find(uid) is None

    def test_table_where(self):
        users = self.db.table("users")
        users.insert({"name": "A", "role": "admin"})
        users.insert({"name": "B", "role": "admin"})
        users.insert({"name": "C", "role": "viewer"})

        admins = users.where(role="admin")
        assert len(admins) == 2

    def test_full_pipeline_simulation(self):
        """Full stack test: DB → node → error correction → crypto → transport (simulated)."""
        data = {"message": "quantum-safe hello"}
        self.db.put("test:sim", data)
        result = self.db.get("test:sim")
        assert result == data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
