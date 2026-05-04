"""
PNEUMA Crypto Layer
===================
Post-quantum key exchange via ML-KEM-768 (FIPS 203) +
symmetric encryption via ChaCha20-Poly1305 (libsodium/pynacl).

Falls back to X25519 + ChaCha20 if liboqs is not installed,
with a clear warning so the user knows they're not quantum-safe.
"""

import os
import hashlib
import time
import struct
import hmac
from typing import Tuple, Optional
from dataclasses import dataclass, field

import nacl.secret
import nacl.utils
import nacl.bindings

# ── Try ML-KEM (liboqs) ──────────────────────────────────────
try:
    import oqs  # pip install liboqs-python
    _HAS_MLKEM = True
except ImportError:
    _HAS_MLKEM = False

MLKEM_LEVEL = "Kyber768"   # NIST Level 3 — ML-KEM-768 equivalent


# ── Key sizes ────────────────────────────────────────────────
MLKEM_PUBKEY_SIZE  = 1184   # ML-KEM-768 encapsulation key
MLKEM_PRIVKEY_SIZE = 2400   # ML-KEM-768 decapsulation key
MLKEM_CT_SIZE      = 1088   # ML-KEM-768 ciphertext
SHARED_SECRET_SIZE = 32     # Shared secret output
SESSION_KEY_SIZE   = 32     # ChaCha20-Poly1305 key


@dataclass
class KeyPair:
    public_key:  bytes   # encapsulation key — share with sender
    private_key: bytes   # decapsulation key — keep secret
    algorithm:   str = "ML-KEM-768"
    created_at:  float = field(default_factory=time.time)


@dataclass
class Session:
    key:        bytes          # 32-byte ChaCha20-Poly1305 session key
    peer_id:    str            # which node this session is with
    created_at: float = field(default_factory=time.time)
    ttl:        int   = 3600  # seconds

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl

    def encrypt(self, plaintext: bytes) -> bytes:
        box = nacl.secret.SecretBox(self.key)
        return bytes(box.encrypt(plaintext))

    def decrypt(self, ciphertext: bytes) -> bytes:
        box = nacl.secret.SecretBox(self.key)
        return bytes(box.decrypt(ciphertext))


class PNEUMACrypto:
    """
    Handles all cryptographic operations for a PNEUMA node.

    Key exchange flow:
      Receiver: ek, dk = generate_keypair()     → share ek with sender
      Sender:   ct, session = encapsulate(ek)   → send ct to receiver
      Receiver: session = decapsulate(dk, ct)   → shared session established
    """

    def __init__(self, mlkem_level: str = "ML_KEM_768"):
        self.mlkem_level = mlkem_level
        if not _HAS_MLKEM:
            print(
                "[PNEUMA WARNING] liboqs not found. "
                "Falling back to X25519 (NOT quantum-safe). "
                "Install: pip install liboqs-python"
            )

    # ── Key generation ───────────────────────────────────────
    def generate_keypair(self) -> KeyPair:
        if _HAS_MLKEM:
            return self._mlkem_keygen()
        else:
            return self._x25519_keygen()

    def _mlkem_keygen(self) -> KeyPair:
        kem = oqs.KeyEncapsulation(MLKEM_LEVEL)
        public_key = kem.generate_keypair()
        private_key = kem.export_secret_key()
        return KeyPair(
            public_key=public_key,
            private_key=private_key,
            algorithm="ML-KEM-768"
        )

    def _x25519_keygen(self) -> KeyPair:
        private_key = nacl.utils.random(32)
        public_key  = nacl.bindings.crypto_scalarmult_base(private_key)
        return KeyPair(
            public_key=public_key,
            private_key=private_key,
            algorithm="X25519-FALLBACK"
        )

    # ── Encapsulation (sender side) ──────────────────────────
    def encapsulate(self, peer_public_key: bytes, peer_id: str) -> Tuple[bytes, Session]:
        """
        Sender calls this with receiver's public key.
        Returns (ciphertext_to_send, session_for_encrypting_data).
        """
        if _HAS_MLKEM:
            return self._mlkem_encaps(peer_public_key, peer_id)
        else:
            return self._x25519_encaps(peer_public_key, peer_id)

    def _mlkem_encaps(self, public_key: bytes, peer_id: str) -> Tuple[bytes, Session]:
        kem = oqs.KeyEncapsulation(MLKEM_LEVEL)
        ciphertext, shared_secret = kem.encap_secret(public_key)
        session_key = self._derive_session_key(shared_secret, peer_id)
        return ciphertext, Session(key=session_key, peer_id=peer_id)

    def _x25519_encaps(self, public_key: bytes, peer_id: str) -> Tuple[bytes, Session]:
        ephemeral_private = nacl.utils.random(32)
        ephemeral_public  = nacl.bindings.crypto_scalarmult_base(ephemeral_private)
        shared_secret     = nacl.bindings.crypto_scalarmult(ephemeral_private, public_key)
        session_key = self._derive_session_key(shared_secret, peer_id)
        return ephemeral_public, Session(key=session_key, peer_id=peer_id)

    # ── Decapsulation (receiver side) ────────────────────────
    def decapsulate(self, keypair: KeyPair, ciphertext: bytes, peer_id: str) -> Session:
        """
        Receiver calls this with their private key and sender's ciphertext.
        Returns session for decrypting data.
        """
        if _HAS_MLKEM:
            return self._mlkem_decaps(keypair, ciphertext, peer_id)
        else:
            return self._x25519_decaps(keypair, ciphertext, peer_id)

    def _mlkem_decaps(self, keypair: KeyPair, ciphertext: bytes, peer_id: str) -> Session:
        kem = oqs.KeyEncapsulation(MLKEM_LEVEL, keypair.private_key)
        shared_secret = kem.decap_secret(ciphertext)
        session_key   = self._derive_session_key(shared_secret, peer_id)
        return Session(key=session_key, peer_id=peer_id)

    def _x25519_decaps(self, keypair: KeyPair, ciphertext: bytes, peer_id: str) -> Session:
        shared_secret = nacl.bindings.crypto_scalarmult(keypair.private_key, ciphertext)
        session_key   = self._derive_session_key(shared_secret, peer_id)
        return Session(key=session_key, peer_id=peer_id)

    # ── Key derivation ───────────────────────────────────────
    def _derive_session_key(self, shared_secret: bytes, peer_id: str) -> bytes:
        """HKDF-SHA3-256 key derivation from shared secret."""
        info  = f"pneuma-v1:{peer_id}".encode()
        salt  = hashlib.sha256(shared_secret).digest()[:32]
        prk   = hmac.new(salt, shared_secret, hashlib.sha3_256).digest()
        key   = hmac.new(prk, info + b"\x01", hashlib.sha3_256).digest()
        return key[:SESSION_KEY_SIZE]

    # ── Convenience: encrypt/decrypt without session object ──
    @staticmethod
    def encrypt(key: bytes, plaintext: bytes) -> bytes:
        box = nacl.secret.SecretBox(key)
        return bytes(box.encrypt(plaintext))

    @staticmethod
    def decrypt(key: bytes, ciphertext: bytes) -> bytes:
        box = nacl.secret.SecretBox(key)
        return bytes(box.decrypt(ciphertext))

    # ── Node identity hash ───────────────────────────────────
    @staticmethod
    def node_id_hash(node_id: str) -> bytes:
        """8-byte node identity — used in packet headers."""
        return hashlib.sha256(node_id.encode()).digest()[:8]
