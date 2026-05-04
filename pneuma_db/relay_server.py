"""
PNEUMA Relay Server
===================
Routes ML-KEM encrypted packets between nodes globally.
The relay NEVER sees plaintext — it only routes ciphertext blobs.

Run this on any VPS / cloud server:
    python -m pneuma_db.relay_server --host 0.0.0.0 --port 8765

Nodes connect via WebSocket and identify with their node_id.
The relay matches DST node_id and forwards the packet.

Architecture:
    Node A  ──ws──►  Relay  ──ws──►  Node B
    (everything encrypted with ML-KEM before it hits the relay)
"""

import asyncio
import json
import logging
import time
import hashlib
import hmac
import os
from typing import Optional
from dataclasses import dataclass, field, asdict

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    raise ImportError("pip install websockets")

try:
    import click
except ImportError:
    raise ImportError("pip install click")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PNEUMA-RELAY] %(levelname)s %(message)s"
)
log = logging.getLogger("pneuma.relay")

# ── Types ────────────────────────────────────────────────────
@dataclass
class ConnectedNode:
    node_id:    str
    ws:         object          # WebSocket connection
    connected_at: float = field(default_factory=time.time)
    last_seen:  float   = field(default_factory=time.time)
    bytes_sent: int     = 0
    bytes_recv: int     = 0
    public_key: Optional[bytes] = None   # ML-KEM encapsulation key


@dataclass
class RelayStats:
    total_connections: int = 0
    active_connections: int = 0
    packets_routed:    int = 0
    bytes_routed:      int = 0
    started_at:        float = field(default_factory=time.time)

    def uptime_seconds(self) -> float:
        return time.time() - self.started_at


# ── Relay Server ─────────────────────────────────────────────
class PNEUMARelayServer:
    """
    Stateless encrypted packet router.
    Nodes register with their node_id.
    Packets are forwarded to the destination node_id.
    The relay never decrypts anything.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8765,
                 secret_key: Optional[str] = None):
        self.host       = host
        self.port       = port
        self.secret_key = secret_key or os.urandom(32).hex()
        self._nodes:    dict[str, ConnectedNode] = {}
        self._stats     = RelayStats()
        self._lock      = asyncio.Lock()

    # ── WebSocket handler ─────────────────────────────────────
    async def handle_connection(self, ws: WebSocketServerProtocol, path: str = "/"):
        node_id = None
        try:
            # First message must be a HELLO with node_id + public key
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)

            if msg.get("type") != "HELLO":
                await ws.send(json.dumps({"type": "ERROR", "msg": "Expected HELLO"}))
                return

            node_id = msg.get("node_id")
            if not node_id:
                await ws.send(json.dumps({"type": "ERROR", "msg": "Missing node_id"}))
                return

            pub_key_hex = msg.get("public_key", "")

            # Register
            async with self._lock:
                node = ConnectedNode(
                    node_id   = node_id,
                    ws        = ws,
                    public_key= bytes.fromhex(pub_key_hex) if pub_key_hex else None,
                )
                self._nodes[node_id] = node
                self._stats.total_connections  += 1
                self._stats.active_connections += 1

            await ws.send(json.dumps({
                "type":    "WELCOME",
                "node_id": node_id,
                "peers":   [n for n in self._nodes if n != node_id],
                "relay":   f"{self.host}:{self.port}",
                "version": "1.0.0",
            }))
            log.info(f"Node connected: {node_id} ({ws.remote_address})")

            # Notify existing nodes of new peer
            await self._broadcast_peer_joined(node_id, pub_key_hex)

            # Main message loop
            async for raw_msg in ws:
                await self._route_message(node_id, raw_msg)

        except asyncio.TimeoutError:
            log.warning(f"Connection timed out waiting for HELLO")
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            log.error(f"Connection error: {e}")
        finally:
            if node_id:
                async with self._lock:
                    self._nodes.pop(node_id, None)
                    self._stats.active_connections -= 1
                log.info(f"Node disconnected: {node_id}")
                await self._broadcast_peer_left(node_id)

    # ── Message routing ───────────────────────────────────────
    async def _route_message(self, src_node_id: str, raw: str | bytes):
        try:
            msg = json.loads(raw)
            msg_type = msg.get("type")

            # Update sender stats
            async with self._lock:
                node = self._nodes.get(src_node_id)
                if node:
                    node.last_seen  = time.time()
                    node.bytes_recv += len(raw)

            if msg_type == "PACKET":
                await self._forward_packet(src_node_id, msg)
            elif msg_type == "BROADCAST":
                await self._broadcast_packet(src_node_id, msg)
            elif msg_type == "PING":
                await self._send_to(src_node_id, {"type": "PONG", "ts": time.time()})
            elif msg_type == "PEERS":
                await self._send_peers_list(src_node_id)
            elif msg_type == "STATS":
                await self._send_stats(src_node_id)

        except json.JSONDecodeError:
            log.warning(f"Invalid JSON from {src_node_id}")
        except Exception as e:
            log.error(f"Route error from {src_node_id}: {e}")

    async def _forward_packet(self, src_id: str, msg: dict):
        """Forward an encrypted packet to its destination node."""
        dst_id  = msg.get("dst")
        payload = msg.get("payload")   # base64 encrypted payload

        if not dst_id or not payload:
            return

        # Relay never inspects payload — just forward it
        envelope = {
            "type":    "PACKET",
            "src":     src_id,
            "dst":     dst_id,
            "payload": payload,
            "ts":      time.time(),
        }

        sent = await self._send_to(dst_id, envelope)

        async with self._lock:
            self._stats.packets_routed += 1
            self._stats.bytes_routed   += len(payload)

        if not sent:
            # Buffer for offline nodes (simple in-memory queue — 100 packets max)
            # Production: use Redis
            log.info(f"Node {dst_id} offline — packet dropped (use persistent queue in prod)")
            await self._send_to(src_id, {
                "type": "DELIVERY_FAIL",
                "dst":  dst_id,
                "reason": "node_offline",
            })

    async def _broadcast_packet(self, src_id: str, msg: dict):
        """Send a packet to all connected nodes except sender."""
        payload = msg.get("payload")
        if not payload:
            return
        envelope = {
            "type":    "BROADCAST",
            "src":     src_id,
            "payload": payload,
            "ts":      time.time(),
        }
        async with self._lock:
            targets = [n for n in self._nodes if n != src_id]

        for node_id in targets:
            await self._send_to(node_id, envelope)

    # ── Peer notifications ───────────────────────────────────
    async def _broadcast_peer_joined(self, new_node_id: str, pub_key_hex: str):
        async with self._lock:
            targets = [n for n in self._nodes if n != new_node_id]
        msg = {
            "type":       "PEER_JOINED",
            "node_id":    new_node_id,
            "public_key": pub_key_hex,
            "ts":         time.time(),
        }
        for node_id in targets:
            await self._send_to(node_id, msg)

    async def _broadcast_peer_left(self, left_node_id: str):
        async with self._lock:
            targets = list(self._nodes.keys())
        msg = {"type": "PEER_LEFT", "node_id": left_node_id, "ts": time.time()}
        for node_id in targets:
            await self._send_to(node_id, msg)

    # ── Utility ──────────────────────────────────────────────
    async def _send_to(self, node_id: str, msg: dict) -> bool:
        async with self._lock:
            node = self._nodes.get(node_id)
        if not node:
            return False
        try:
            raw = json.dumps(msg)
            await node.ws.send(raw)
            async with self._lock:
                node.bytes_sent += len(raw)
            return True
        except Exception:
            return False

    async def _send_peers_list(self, node_id: str):
        async with self._lock:
            peers = {
                nid: {"connected_at": n.connected_at, "last_seen": n.last_seen}
                for nid, n in self._nodes.items()
                if nid != node_id
            }
        await self._send_to(node_id, {"type": "PEERS", "peers": peers})

    async def _send_stats(self, node_id: str):
        await self._send_to(node_id, {
            "type":   "STATS",
            "uptime": self._stats.uptime_seconds(),
            "active": self._stats.active_connections,
            "total":  self._stats.total_connections,
            "routed": self._stats.packets_routed,
            "bytes":  self._stats.bytes_routed,
        })

    # ── Start ────────────────────────────────────────────────
    async def start(self):
        log.info(f"PNEUMA Relay Server starting on ws://{self.host}:{self.port}")
        log.info("Relay never decrypts packets — pure ML-KEM encrypted routing")
        async with websockets.serve(self.handle_connection, self.host, self.port):
            log.info(f"PNEUMA Relay ready — ws://{self.host}:{self.port}")
            await asyncio.Future()   # run forever


# ── CLI ───────────────────────────────────────────────────────
@click.command()
@click.option("--host",   default="0.0.0.0",  show_default=True, help="Bind address")
@click.option("--port",   default=8765,        show_default=True, help="WebSocket port")
@click.option("--secret", default=None,        help="Server secret key (auto-generated if omitted)")
def main(host: str, port: int, secret: Optional[str]):
    """Start the PNEUMA Relay Server."""
    server = PNEUMARelayServer(host=host, port=port, secret_key=secret)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()
