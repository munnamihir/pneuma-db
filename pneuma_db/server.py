"""
PNEUMA-DB REST API Server
=========================
Exposes PNEUMA-DB as a local REST API so any language
(Node.js, Python, Go, Rust, etc.) can use it.

Start it with:
    pneuma-db server --node-id mynode --relay ws://relay.pneuma.io:8765

Then from Node.js:
    import { PneumaDB } from 'pneuma-db-client'
    const db = new PneumaDB({ port: 7723 })
    await db.put('user:001', { name: 'Alice' })
"""

import asyncio
import logging
from typing import Any, Optional, List
from contextlib import asynccontextmanager

try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    raise ImportError("pip install fastapi uvicorn pydantic")

from .node import PNEUMANode
from .db   import PNEUMA_DB

log = logging.getLogger("pneuma.server")

# Global DB instance
_db: Optional[PNEUMA_DB] = None


# ── Request / Response models ──────────────────────────────────
class PutRequest(BaseModel):
    key:   str
    value: Any
    ttl:   Optional[int] = None

class GetResponse(BaseModel):
    key:   str
    value: Any
    found: bool

class CASRequest(BaseModel):
    key:       str
    expected:  Any
    new_value: Any

class ScanResponse(BaseModel):
    prefix:  str
    results: dict[str, Any]
    count:   int

class InsertRequest(BaseModel):
    data: dict[str, Any]
    ttl:  Optional[int] = None

class UpdateRequest(BaseModel):
    fields: dict[str, Any]

class WhereRequest(BaseModel):
    filters: dict[str, Any]


# ── FastAPI app ───────────────────────────────────────────────
def create_app(db: PNEUMA_DB) -> FastAPI:
    global _db
    _db = db

    app = FastAPI(
        title       = "PNEUMA-DB API",
        description = "Post-quantum distributed database over ultrasonic air",
        version     = "1.0.0",
        docs_url    = "/docs",
        redoc_url   = "/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins     = ["*"],
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    # ── Health ───────────────────────────────────────────────
    @app.get("/health", tags=["meta"])
    def health():
        return {
            "status":   "ok",
            "version":  "1.0.0",
            "protocol": "ML-KEM-768",
            "node_id":  _db.node.node_id,
        }

    @app.get("/stats", tags=["meta"])
    def stats():
        return _db.stats()

    @app.get("/nodes", tags=["meta"])
    def nodes():
        return {"nodes": _db.node_status()}

    # ── Core KV ──────────────────────────────────────────────
    @app.put("/db", tags=["kv"])
    def put(req: PutRequest):
        success = _db.put(req.key, req.value, ttl=req.ttl)
        if not success:
            raise HTTPException(500, "Write failed")
        return {"ok": True, "key": req.key}

    @app.get("/db/{key:path}", tags=["kv"])
    def get(key: str):
        value = _db.get(key)
        return GetResponse(key=key, value=value, found=value is not None)

    @app.delete("/db/{key:path}", tags=["kv"])
    def delete(key: str):
        _db.delete(key)
        return {"ok": True, "key": key}

    @app.post("/db/cas", tags=["kv"])
    def cas(req: CASRequest):
        success = _db.cas(req.key, req.expected, req.new_value)
        return {"ok": success, "key": req.key}

    @app.get("/db/scan/{prefix:path}", tags=["kv"])
    def scan(prefix: str):
        results = _db.scan_prefix(prefix)
        return ScanResponse(prefix=prefix, results=results, count=len(results))

    # ── Table ORM ────────────────────────────────────────────
    @app.post("/table/{table_name}", tags=["table"])
    def table_insert(table_name: str, req: InsertRequest):
        """Insert a record into a named table."""
        table     = _db.table(table_name)
        record_id = table.insert(req.data, ttl=req.ttl)
        return {"ok": True, "id": record_id}

    @app.get("/table/{table_name}/{record_id}", tags=["table"])
    def table_find(table_name: str, record_id: str):
        """Find a record by ID."""
        table  = _db.table(table_name)
        record = table.find(record_id)
        if not record:
            raise HTTPException(404, f"Record {record_id} not found in {table_name}")
        return record

    @app.get("/table/{table_name}", tags=["table"])
    def table_all(table_name: str):
        """Get all records in a table."""
        table = _db.table(table_name)
        return {"records": table.all(), "count": table.count()}

    @app.post("/table/{table_name}/where", tags=["table"])
    def table_where(table_name: str, req: WhereRequest):
        """Filter records by field values."""
        table   = _db.table(table_name)
        results = table.where(**req.filters)
        return {"records": results, "count": len(results)}

    @app.patch("/table/{table_name}/{record_id}", tags=["table"])
    def table_update(table_name: str, record_id: str, req: UpdateRequest):
        """Update fields on a record."""
        table   = _db.table(table_name)
        success = table.update(record_id, **req.fields)
        if not success:
            raise HTTPException(404, f"Record {record_id} not found")
        return {"ok": True, "id": record_id}

    @app.delete("/table/{table_name}/{record_id}", tags=["table"])
    def table_delete(table_name: str, record_id: str):
        """Delete a record from a table."""
        table = _db.table(table_name)
        table.delete(record_id)
        return {"ok": True, "id": record_id}

    @app.get("/table/{table_name}/next-id", tags=["table"])
    def table_next_id(table_name: str):
        """Get the next auto-increment ID for a table."""
        return {"id": _db.next_id(table_name)}

    return app


# ── Server factory ────────────────────────────────────────────
def run_server(
    node_id:     str,
    peers:       List[str],
    relay_url:   Optional[str] = None,
    host:        str   = "127.0.0.1",
    port:        int   = 7723,
    mlkem_level: str   = "ML_KEM_768",
    symbol_ms:   int   = 100,
    replication: int   = 3,
    db_path:     Optional[str] = None,
):
    # Build node
    node = PNEUMANode(
        node_id     = node_id,
        known_nodes = peers + [node_id],
        mlkem_level = mlkem_level,
        replication = replication,
        symbol_ms   = symbol_ms,
    )

    # Build DB
    db = PNEUMA_DB(node, relay_url=relay_url, db_path=db_path)

    # Connect to relay if configured
    if relay_url:
        log.info(f"Connecting to relay: {relay_url}")
        db.connect_relay_sync()

    # Start REST server
    app = create_app(db)
    log.info(f"PNEUMA-DB REST API on http://{host}:{port}/docs")
    uvicorn.run(app, host=host, port=port, log_level="warning")
