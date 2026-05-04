"""
PNEUMA-DB
=========
Post-quantum, networkless, encrypted distributed database.
Acoustic local mode + global WebSocket relay mode.

Quick start:
    from pneuma_db import PNEUMA_DB, PNEUMANode, Table

    node = PNEUMANode("my-node", ["my-node"])
    db   = PNEUMA_DB(node, relay_url="ws://relay.pneuma.io:8765")

    db.put("user:001", {"name": "Alice"})
    print(db.get("user:001"))

    users = db.table("users")
    uid   = users.insert({"name": "Bob", "role": "admin"})
    print(users.find(uid))
"""

from .node   import PNEUMANode, HashRing, NodeStatus
from .db     import PNEUMA_DB, Table, LocalStore
from .crypto import PNEUMACrypto, KeyPair, Session
from .transport     import PNEUMATransport
from .framing       import Framer, Packet, Flags
from .error_correction import ErrorCorrection

__version__ = "1.0.0"
__author__  = "PNEUMA"
__all__     = [
    "PNEUMA_DB",
    "PNEUMANode",
    "Table",
    "LocalStore",
    "PNEUMACrypto",
    "KeyPair",
    "Session",
    "PNEUMATransport",
    "Framer",
    "Packet",
    "Flags",
    "ErrorCorrection",
    "HashRing",
    "NodeStatus",
]
