"""
PNEUMA-DB
=========
Distributed key-value store over ultrasonic air (local)
and ML-KEM encrypted WebSocket relay (global).

Modes:
  LOCAL  — acoustic FSK only (3–15m range)
  RELAY  — WebSocket relay, ML-KEM end-to-end (global range)
  HYBRID — acoustic for key bootstrap, relay for data (recommended)

Usage (local):
    db = PNEUMA_DB(node)
    db.put("user:001", {"name": "Alice"})
    db.get("user:001")

Usage (global relay):
    db = PNEUMA_DB(node, relay_url="ws://relay.pneuma.io:8765")
    await db.connect_relay()
    db.put("user:001", {"name": "Alice"})
    db.get("user:001")
"""

import asyncio
import json
import sqlite3
import time
import hashlib
import threading
import uuid
import logging
from enum import Enum
from typing import Any, Optional, List
from dataclasses import dataclass

from .node   import PNEUMANode, NodeStatus
from .crypto import PNEUMACrypto, Session

log = logging.getLogger("pneuma.db")


# ── Operation codes ───────────────────────────────────────────
class OpCode(str, Enum):
    PUT    = "PUT"
    GET    = "GET"
    DELETE = "DELETE"
    CAS    = "CAS"
    REPLY  = "REPLY"
    ACK    = "ACK"
    SYNC   = "SYNC"
    SCAN   = "SCAN"


@dataclass
class DBRequest:
    op:        OpCode
    key:       str
    value:     Any       = None
    expected:  Any       = None
    request_id: str      = ""
    sender:    str       = ""
    ttl:       Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "op":         self.op.value,
            "key":        self.key,
            "value":      self.value,
            "expected":   self.expected,
            "request_id": self.request_id,
            "sender":     self.sender,
            "ttl":        self.ttl,
        }

    @staticmethod
    def from_dict(d: dict) -> "DBRequest":
        return DBRequest(
            op         = OpCode(d["op"]),
            key        = d["key"],
            value      = d.get("value"),
            expected   = d.get("expected"),
            request_id = d.get("request_id", ""),
            sender     = d.get("sender", ""),
            ttl        = d.get("ttl"),
        )


# ── Local SQLite storage ──────────────────────────────────────
class LocalStore:
    """SQLite-backed local shard for one PNEUMA-DB node."""

    def __init__(self, node_id: str, db_path: Optional[str] = None):
        path = db_path or f"pneuma_{node_id.replace('/', '_')}.db"
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init()

    def _init(self):
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS store (
                    key         TEXT PRIMARY KEY,
                    value       TEXT NOT NULL,
                    version     INTEGER DEFAULT 1,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL,
                    expires_at  REAL,
                    checksum    TEXT NOT NULL
                )
            """)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON store(expires_at)")
            self.conn.commit()

    def _checksum(self, value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:16]

    def put(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        serialized = json.dumps(value)
        checksum   = self._checksum(serialized)
        now        = time.time()
        expires_at = now + ttl if ttl else None
        with self._lock:
            try:
                self.conn.execute("""
                    INSERT INTO store (key, value, version, created_at, updated_at, expires_at, checksum)
                    VALUES (?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value      = excluded.value,
                        version    = version + 1,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at,
                        checksum   = excluded.checksum
                """, (key, serialized, now, now, expires_at, checksum))
                self.conn.commit()
                return True
            except Exception as e:
                log.error(f"LocalStore.put error: {e}")
                return False

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            row = self.conn.execute(
                "SELECT value, expires_at FROM store WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        value_str, expires_at = row
        if expires_at and time.time() > expires_at:
            self.delete(key)
            return None
        return json.loads(value_str)

    def delete(self, key: str) -> bool:
        with self._lock:
            self.conn.execute("DELETE FROM store WHERE key = ?", (key,))
            self.conn.commit()
        return True

    def cas(self, key: str, expected: Any, new_value: Any) -> bool:
        """Atomic compare-and-swap."""
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM store WHERE key = ?", (key,)
            ).fetchone()
            current = json.loads(row[0]) if row else None
            if current != expected:
                return False
            serialized = json.dumps(new_value)
            now        = time.time()
            self.conn.execute("""
                INSERT INTO store (key, value, version, created_at, updated_at, expires_at, checksum)
                VALUES (?, ?, 1, ?, ?, NULL, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, version = version + 1,
                    updated_at = excluded.updated_at, checksum = excluded.checksum
            """, (key, serialized, now, now, self._checksum(serialized)))
            self.conn.commit()
            return True

    def scan_prefix(self, prefix: str) -> dict[str, Any]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT key, value, expires_at FROM store WHERE key LIKE ?",
                (prefix + "%",)
            ).fetchall()
        now = time.time()
        return {
            key: json.loads(val)
            for key, val, exp in rows
            if not (exp and now > exp)
        }

    def next_id(self, table: str) -> int:
        """Atomic auto-increment counter per table."""
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM store WHERE key = ?", (f"_seq:{table}",)
            ).fetchone()
            current  = int(json.loads(row[0])) if row else 0
            next_val = current + 1
            now      = time.time()
            self.conn.execute("""
                INSERT INTO store (key, value, version, created_at, updated_at, expires_at, checksum)
                VALUES (?, ?, 1, ?, ?, NULL, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, checksum = excluded.checksum
            """, (f"_seq:{table}", json.dumps(next_val), now, now, self._checksum(str(next_val))))
            self.conn.commit()
            return next_val

    def count(self) -> int:
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM store").fetchone()[0]

    def expire_old(self):
        """Remove expired keys. Call periodically."""
        with self._lock:
            self.conn.execute("DELETE FROM store WHERE expires_at IS NOT NULL AND expires_at < ?", (time.time(),))
            self.conn.commit()


# ── Table ORM helper ──────────────────────────────────────────
class Table:
    """
    ORM-style wrapper for a logical 'table' in PNEUMA-DB.
    Provides insert, find, all, where, update, delete.
    """

    def __init__(self, db: "PNEUMA_DB", name: str):
        self.db   = db
        self.name = name

    def _key(self, record_id: str) -> str:
        return f"{self.name}:{record_id}"

    def insert(self, data: dict, ttl: Optional[int] = None) -> str:
        record_id          = data.get("id") or str(uuid.uuid4())[:8]
        data["id"]         = record_id
        data["created_at"] = data.get("created_at", time.time())
        data["updated_at"] = time.time()
        self.db.put(self._key(record_id), data, ttl=ttl)
        # Maintain ID index
        all_ids = self.db.get(f"_ids:{self.name}") or []
        if record_id not in all_ids:
            all_ids.append(record_id)
            self.db.put(f"_ids:{self.name}", all_ids)
        return record_id

    def find(self, record_id: str) -> Optional[dict]:
        return self.db.get(self._key(record_id))

    def all(self) -> List[dict]:
        ids     = self.db.get(f"_ids:{self.name}") or []
        records = [self.db.get(self._key(i)) for i in ids]
        return [r for r in records if r is not None]

    def where(self, **filters) -> List[dict]:
        return [r for r in self.all() if all(r.get(k) == v for k, v in filters.items())]

    def find_by(self, field: str, value: Any) -> Optional[dict]:
        """Lookup via index (O(1)) if index exists, else linear scan."""
        indexed = self.db.get(f"idx:{self.name}:{field}:{value}")
        if indexed:
            return self.find(indexed)
        # Linear fallback
        results = self.where(**{field: value})
        return results[0] if results else None

    def update(self, record_id: str, **fields) -> bool:
        record = self.find(record_id)
        if not record:
            return False
        record.update(fields)
        record["updated_at"] = time.time()
        return self.db.put(self._key(record_id), record)

    def delete(self, record_id: str) -> bool:
        self.db.delete(self._key(record_id))
        all_ids = self.db.get(f"_ids:{self.name}") or []
        self.db.put(f"_ids:{self.name}", [i for i in all_ids if i != record_id])
        return True

    def add_index(self, field: str, record_id: str, value: Any):
        """Create a field-level index for O(1) lookup."""
        self.db.put(f"idx:{self.name}:{field}:{value}", record_id)

    def count(self) -> int:
        return len(self.db.get(f"_ids:{self.name}") or [])


# ── PNEUMA_DB ────────────────────────────────────────────────
class PNEUMA_DB:
    """
    Main PNEUMA-DB interface.

    Works in three modes:
      1. LOCAL  — pure acoustic (no internet needed)
      2. RELAY  — global via WebSocket relay (internet)
      3. HYBRID — acoustic key bootstrap + relay data (default for global)
    """

    def __init__(
        self,
        node:          PNEUMANode,
        relay_url:     Optional[str] = None,
        db_path:       Optional[str] = None,
    ):
        self.node      = node
        self.relay_url = relay_url
        self.store     = LocalStore(node.node_id, db_path)
        self._relay:   Optional[object] = None   # RelayClient (lazy import)
        self._loop:    Optional[asyncio.AbstractEventLoop] = None
        self._relay_thread: Optional[threading.Thread] = None

        # Pending remote replies: request_id → asyncio.Future
        self._pending: dict[str, asyncio.Future] = {}

        log.info(f"PNEUMA-DB node '{node.node_id}' initialized")
        log.info(f"Mode: {'HYBRID (local + relay)' if relay_url else 'LOCAL (acoustic only)'}")

    # ── Relay connection ──────────────────────────────────────
    def connect_relay_sync(self):
        """
        Connect to relay server in a background thread.
        Call this from synchronous code (e.g. server startup).
        """
        if not self.relay_url:
            raise ValueError("No relay_url configured")

        self._loop = asyncio.new_event_loop()

        def run():
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._relay_loop())

        self._relay_thread = threading.Thread(target=run, daemon=True)
        self._relay_thread.start()
        time.sleep(1.5)   # Give relay time to connect
        log.info("Relay connection started in background thread")

    async def connect_relay(self):
        """Connect to relay server (async version)."""
        if not self.relay_url:
            raise ValueError("No relay_url configured")
        await self._relay_loop()

    async def _relay_loop(self):
        from .relay_client import RelayClient
        self._relay = RelayClient(
            node_id    = self.node.node_id,
            relay_url  = self.relay_url,
            public_key = self.node.session_keypair.public_key,
            on_packet  = self._on_relay_packet,
            on_peer_joined = self._on_peer_joined,
            on_peer_left   = self._on_peer_left,
        )
        await self._relay.connect()

    async def _on_relay_packet(self, src_node_id: str, encrypted: bytes):
        """Handle an incoming encrypted packet from the relay."""
        try:
            session = self.node._sessions.get(src_node_id)
            if not session:
                log.warning(f"No session for {src_node_id} — cannot decrypt")
                return

            raw     = session.decrypt(encrypted)
            msg     = json.loads(raw)
            await self._handle_db_message(src_node_id, msg)

        except Exception as e:
            log.error(f"Relay packet handler error from {src_node_id}: {e}")

    async def _on_peer_joined(self, peer_id: str, pub_key: bytes):
        """When a peer joins the relay, initiate ML-KEM key exchange."""
        if pub_key:
            self.node.add_peer(peer_id)
            try:
                ciphertext, session = self.node.crypto.encapsulate(pub_key, peer_id)
                self.node._sessions[peer_id] = session
                # Send the KEM ciphertext to the new peer
                key_msg = json.dumps({
                    "op":         "KEM_INIT",
                    "ciphertext": ciphertext.hex(),
                    "sender":     self.node.node_id,
                }).encode()
                # Encrypt the key_msg with a temporary shared key derived from ciphertext hash
                await self._relay.send(peer_id, key_msg)
                log.info(f"ML-KEM session established with {peer_id}")
            except Exception as e:
                log.error(f"Key exchange with {peer_id} failed: {e}")

    async def _on_peer_left(self, peer_id: str):
        self.node._sessions.pop(peer_id, None)

    # ── Message handling ──────────────────────────────────────
    async def _handle_db_message(self, src_id: str, msg: dict):
        """Handle an incoming DB operation from a remote node."""
        op = msg.get("op")

        if op == "PUT":
            req = DBRequest.from_dict(msg)
            self.store.put(req.key, req.value, req.ttl)
            await self._send_ack(src_id, req.request_id, True)

        elif op == "GET":
            req   = DBRequest.from_dict(msg)
            value = self.store.get(req.key)
            await self._send_reply(src_id, req.request_id, value)

        elif op == "DELETE":
            req = DBRequest.from_dict(msg)
            self.store.delete(req.key)
            await self._send_ack(src_id, req.request_id, True)

        elif op == "CAS":
            req     = DBRequest.from_dict(msg)
            success = self.store.cas(req.key, req.expected, req.value)
            await self._send_ack(src_id, req.request_id, success)

        elif op in ("REPLY", "ACK"):
            request_id = msg.get("request_id")
            future     = self._pending.pop(request_id, None)
            if future and not future.done():
                future.set_result(msg.get("value") if op == "REPLY" else msg.get("ok"))

    async def _send_reply(self, dst_id: str, request_id: str, value: Any):
        msg = json.dumps({"op": "REPLY", "request_id": request_id, "value": value}).encode()
        await self._send_encrypted(dst_id, msg)

    async def _send_ack(self, dst_id: str, request_id: str, ok: bool):
        msg = json.dumps({"op": "ACK", "request_id": request_id, "ok": ok}).encode()
        await self._send_encrypted(dst_id, msg)

    async def _send_encrypted(self, dst_id: str, plaintext: bytes):
        session = self.node._sessions.get(dst_id)
        if not session:
            raise RuntimeError(f"No session with {dst_id}")
        encrypted = session.encrypt(plaintext)
        await self._relay.send(dst_id, encrypted)

    def _send_relay_sync(self, dst_id: str, msg: dict, timeout: float = 10.0) -> Any:
        """Synchronous wrapper for relay sends with reply waiting."""
        if not self._relay or not self._relay.connected:
            raise RuntimeError("Relay not connected — call connect_relay_sync() first")

        request_id = str(uuid.uuid4())[:8]
        msg["request_id"] = request_id
        msg["sender"]     = self.node.node_id

        future = asyncio.run_coroutine_threadsafe(
            self._send_and_wait(dst_id, msg, request_id, timeout),
            self._loop
        )
        return future.result(timeout=timeout + 2)

    async def _send_and_wait(self, dst_id: str, msg: dict, request_id: str, timeout: float) -> Any:
        loop   = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending[request_id] = future

        try:
            plaintext = json.dumps(msg).encode()
            await self._send_encrypted(dst_id, plaintext)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            return None

    # ── Public API — works local OR relay ─────────────────────
    def put(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Write a key-value pair.
        Routes to owner node — locally if same node, via relay if remote.
        """
        owner = self.node.owner_of(key)

        if owner == self.node.node_id:
            # We own it — store locally
            success = self.store.put(key, value, ttl)
            # Replicate to other nodes
            replicas = [n for n in self.node.replicas_of(key) if n != self.node.node_id]
            for replica_id in replicas:
                self._replicate(replica_id, key, value, ttl)
            return success
        else:
            # Route to owner
            if self._relay and self._relay.connected:
                result = self._send_relay_sync(owner, {
                    "op": "PUT", "key": key, "value": value, "ttl": ttl
                })
                return bool(result)
            else:
                # Fallback: store locally if relay unavailable
                log.warning(f"Relay unavailable — storing {key} locally (not on owner {owner})")
                return self.store.put(key, value, ttl)

    def get(self, key: str) -> Optional[Any]:
        """Read a value by key. Routes to owner if necessary."""
        # Check local store first (may have replica)
        local = self.store.get(key)
        if local is not None:
            return local

        owner = self.node.owner_of(key)
        if owner == self.node.node_id:
            return None   # We own it and don't have it

        # Route to owner via relay
        if self._relay and self._relay.connected:
            return self._send_relay_sync(owner, {"op": "GET", "key": key})

        return None

    def delete(self, key: str) -> bool:
        """Delete a key. Propagates to replicas."""
        self.store.delete(key)
        owner = self.node.owner_of(key)
        if owner != self.node.node_id and self._relay and self._relay.connected:
            self._send_relay_sync(owner, {"op": "DELETE", "key": key})
        return True

    def cas(self, key: str, expected: Any, new_value: Any) -> bool:
        """Atomic compare-and-swap."""
        owner = self.node.owner_of(key)
        if owner == self.node.node_id:
            return self.store.cas(key, expected, new_value)
        if self._relay and self._relay.connected:
            result = self._send_relay_sync(owner, {
                "op": "CAS", "key": key, "expected": expected, "value": new_value
            })
            return bool(result)
        return self.store.cas(key, expected, new_value)

    def scan_prefix(self, prefix: str) -> dict[str, Any]:
        """Scan all keys matching prefix (local shard only)."""
        return self.store.scan_prefix(prefix)

    def next_id(self, table: str) -> int:
        """Auto-increment counter for a table."""
        return self.store.next_id(table)

    def table(self, name: str) -> Table:
        """Get an ORM-style Table wrapper."""
        return Table(self, name)

    def node_status(self) -> List[dict]:
        return [s.to_dict() for s in self.node.node_status()]

    def stats(self) -> dict:
        return {
            "node_id":       self.node.node_id,
            "local_records": self.store.count(),
            "relay_connected": bool(self._relay and self._relay.connected),
            "relay_url":     self.relay_url,
            "known_nodes":   self.node.known_nodes,
            "mode":          "HYBRID" if self.relay_url else "LOCAL",
        }

    def _replicate(self, node_id: str, key: str, value: Any, ttl: Optional[int]):
        """Best-effort replication to a replica node."""
        if self._relay and self._relay.connected:
            try:
                self._send_relay_sync(node_id, {"op": "PUT", "key": key, "value": value, "ttl": ttl})
            except Exception:
                pass   # Replication is best-effort

    # ── Context manager ───────────────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self._relay:
            asyncio.run_coroutine_threadsafe(self._relay.disconnect(), self._loop)
