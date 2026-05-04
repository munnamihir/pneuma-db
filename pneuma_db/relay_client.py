"""
PNEUMA Relay Client
===================
Connects a PNEUMA node to the global relay server via WebSocket.
All payloads are ML-KEM encrypted before leaving the device —
the relay only sees opaque ciphertext blobs.

Usage:
    client = RelayClient(
        node_id    = "my-node",
        relay_url  = "ws://relay.pneuma.io:8765",
        crypto     = pneuma_node.crypto,
        session_key= my_session_key,
    )
    await client.connect()
    await client.send("peer-node", encrypted_bytes)
"""

import asyncio
import json
import base64
import logging
import time
from typing import Optional, Callable, Awaitable

try:
    import websockets
except ImportError:
    raise ImportError("pip install websockets")

log = logging.getLogger("pneuma.relay_client")


class RelayClient:
    """
    Manages a WebSocket connection to a PNEUMA relay server.

    The relay client:
      1. Connects and registers this node's ID + public key.
      2. Listens for incoming encrypted packets.
      3. Sends outgoing encrypted packets to named peers.
      4. Auto-reconnects on disconnection.
    """

    def __init__(
        self,
        node_id:     str,
        relay_url:   str,
        public_key:  bytes,              # ML-KEM encapsulation key to share
        on_packet:   Callable[[str, bytes], Awaitable[None]],  # (src_node_id, encrypted_bytes)
        on_peer_joined: Optional[Callable[[str, bytes], Awaitable[None]]] = None,
        on_peer_left:   Optional[Callable[[str], Awaitable[None]]] = None,
        reconnect_delay: float = 5.0,
    ):
        self.node_id         = node_id
        self.relay_url       = relay_url
        self.public_key      = public_key
        self.on_packet       = on_packet
        self.on_peer_joined  = on_peer_joined
        self.on_peer_left    = on_peer_left
        self.reconnect_delay = reconnect_delay

        self._ws:        Optional[object] = None
        self._connected  = False
        self._peers:     dict[str, bytes] = {}    # peer_id → public_key
        self._running    = False
        self._send_queue: asyncio.Queue = asyncio.Queue()

    # ── Connect & run ─────────────────────────────────────────
    async def connect(self):
        """Start the relay connection (runs forever, auto-reconnects)."""
        self._running = True
        while self._running:
            try:
                await self._run_connection()
            except Exception as e:
                log.warning(f"Relay disconnected: {e}. Reconnecting in {self.reconnect_delay}s…")
                self._connected = False
                await asyncio.sleep(self.reconnect_delay)

    async def _run_connection(self):
        async with websockets.connect(self.relay_url, ping_interval=30) as ws:
            self._ws = ws

            # Register with relay
            await ws.send(json.dumps({
                "type":       "HELLO",
                "node_id":    self.node_id,
                "public_key": self.public_key.hex(),
                "version":    "1.0.0",
            }))

            # Wait for WELCOME
            raw     = await ws.recv()
            welcome = json.loads(raw)
            if welcome.get("type") != "WELCOME":
                raise RuntimeError(f"Bad handshake: {welcome}")

            self._connected = True
            log.info(f"Connected to relay {self.relay_url} as '{self.node_id}'")
            log.info(f"Peers online: {welcome.get('peers', [])}")

            # Start sender task
            sender_task = asyncio.create_task(self._sender_loop(ws))

            try:
                # Receive loop
                async for raw_msg in ws:
                    await self._handle_message(json.loads(raw_msg))
            finally:
                sender_task.cancel()
                self._connected = False

    # ── Message handling ──────────────────────────────────────
    async def _handle_message(self, msg: dict):
        msg_type = msg.get("type")

        if msg_type == "PACKET":
            src     = msg.get("src", "unknown")
            payload = msg.get("payload", "")
            try:
                encrypted = base64.b64decode(payload)
                await self.on_packet(src, encrypted)
            except Exception as e:
                log.error(f"Failed to handle packet from {src}: {e}")

        elif msg_type == "PEER_JOINED":
            peer_id   = msg.get("node_id")
            pub_hex   = msg.get("public_key", "")
            pub_key   = bytes.fromhex(pub_hex) if pub_hex else b""
            if peer_id:
                self._peers[peer_id] = pub_key
                log.info(f"Peer joined: {peer_id}")
                if self.on_peer_joined:
                    await self.on_peer_joined(peer_id, pub_key)

        elif msg_type == "PEER_LEFT":
            peer_id = msg.get("node_id")
            if peer_id:
                self._peers.pop(peer_id, None)
                log.info(f"Peer left: {peer_id}")
                if self.on_peer_left:
                    await self.on_peer_left(peer_id)

        elif msg_type == "DELIVERY_FAIL":
            log.warning(f"Delivery failed to {msg.get('dst')}: {msg.get('reason')}")

        elif msg_type == "BROADCAST":
            src     = msg.get("src", "broadcast")
            payload = msg.get("payload", "")
            try:
                encrypted = base64.b64decode(payload)
                await self.on_packet(src, encrypted)
            except Exception as e:
                log.error(f"Broadcast handler error: {e}")

        elif msg_type == "PONG":
            pass   # heartbeat acknowledged

    # ── Sender loop ──────────────────────────────────────────
    async def _sender_loop(self, ws):
        """Drain the outbound queue and send to relay."""
        while True:
            envelope = await self._send_queue.get()
            try:
                await ws.send(json.dumps(envelope))
            except Exception as e:
                log.error(f"Send failed: {e}")

    # ── Public API ────────────────────────────────────────────
    async def send(self, dst_node_id: str, encrypted_payload: bytes):
        """
        Queue an encrypted packet for delivery to dst_node_id.
        Payload must already be ML-KEM encrypted — relay never decrypts.
        """
        if not self._connected:
            raise RuntimeError("Relay not connected")

        envelope = {
            "type":    "PACKET",
            "dst":     dst_node_id,
            "payload": base64.b64encode(encrypted_payload).decode(),
            "ts":      time.time(),
        }
        await self._send_queue.put(envelope)

    async def broadcast(self, encrypted_payload: bytes):
        """Send an encrypted packet to ALL connected peers."""
        if not self._connected:
            raise RuntimeError("Relay not connected")

        envelope = {
            "type":    "BROADCAST",
            "payload": base64.b64encode(encrypted_payload).decode(),
            "ts":      time.time(),
        }
        await self._send_queue.put(envelope)

    async def ping(self):
        """Send a ping to measure relay latency."""
        await self._send_queue.put({"type": "PING", "ts": time.time()})

    async def disconnect(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def peers(self) -> list[str]:
        return list(self._peers.keys())

    def peer_public_key(self, peer_id: str) -> Optional[bytes]:
        return self._peers.get(peer_id)
