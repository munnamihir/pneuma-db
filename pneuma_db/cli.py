"""
PNEUMA-DB CLI
=============
Commands:
    pneuma-db server   - Start the REST API server (with optional relay)
    pneuma-db relay    - Start the relay server
    pneuma-db put      - Write a key-value pair
    pneuma-db get      - Read a value
    pneuma-db delete   - Delete a key
    pneuma-db scan     - Scan by key prefix
    pneuma-db stats    - Show node statistics
    pneuma-db ping     - Check relay connectivity
"""

import json
import sys
import click
import requests
from typing import Optional


API_BASE = "http://127.0.0.1:7723"


def _api(method: str, path: str, **kwargs) -> dict:
    try:
        resp = requests.request(method, f"{API_BASE}{path}", **kwargs, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        click.echo(f"[ERROR] Cannot connect to PNEUMA-DB at {API_BASE}", err=True)
        click.echo("Is the server running? Try: pneuma-db server --node-id mynode", err=True)
        sys.exit(1)
    except requests.HTTPError as e:
        click.echo(f"[ERROR] {e.response.status_code}: {e.response.text}", err=True)
        sys.exit(1)


@click.group()
@click.option("--host", default="127.0.0.1", envvar="PNEUMA_HOST", help="API server host")
@click.option("--port", default=7723,         envvar="PNEUMA_PORT", help="API server port")
@click.pass_context
def cli(ctx, host, port):
    """PNEUMA-DB — Post-quantum distributed database over air."""
    global API_BASE
    API_BASE = f"http://{host}:{port}"
    ctx.ensure_object(dict)


# ── server ────────────────────────────────────────────────────
@cli.command()
@click.option("--node-id",     required=True,              help="Unique node identifier")
@click.option("--peers",       default="",                  help="Comma-separated peer node IDs")
@click.option("--relay",       default=None, envvar="PNEUMA_RELAY_URL", help="Relay WebSocket URL (for global access)")
@click.option("--host",        default="127.0.0.1",         help="REST API bind address")
@click.option("--port",        default=7723,                help="REST API port")
@click.option("--replication", default=3,                   help="Replication factor")
@click.option("--symbol-ms",   default=100,                 help="FSK symbol duration (ms)")
@click.option("--db-path",     default=None,                help="SQLite database file path")
def server(node_id, peers, relay, host, port, replication, symbol_ms, db_path):
    """Start the PNEUMA-DB REST API server."""
    from .server import run_server

    peer_list = [p.strip() for p in peers.split(",") if p.strip()]

    click.echo(f"Starting PNEUMA-DB node: {node_id}")
    click.echo(f"REST API: http://{host}:{port}/docs")
    if relay:
        click.echo(f"Relay: {relay} (global access enabled)")
    else:
        click.echo("Mode: LOCAL (acoustic only — add --relay for global access)")
    if peer_list:
        click.echo(f"Peers: {', '.join(peer_list)}")

    run_server(
        node_id     = node_id,
        peers       = peer_list,
        relay_url   = relay,
        host        = host,
        port        = port,
        replication = replication,
        symbol_ms   = symbol_ms,
        db_path     = db_path,
    )


# ── relay ─────────────────────────────────────────────────────
@cli.command()
@click.option("--host",   default="0.0.0.0", help="Relay bind address")
@click.option("--port",   default=8765,       help="Relay WebSocket port")
@click.option("--secret", default=None,       help="Server secret (auto-generated if omitted)")
def relay(host, port, secret):
    """Start the PNEUMA Relay Server (deploy on a VPS for global access)."""
    import asyncio
    from .relay_server import PNEUMARelayServer

    click.echo(f"Starting PNEUMA Relay Server on ws://{host}:{port}")
    click.echo("Relay is stateless — it never decrypts packets.")
    click.echo("Deploy on any VPS: DigitalOcean, AWS, Hetzner, etc.")

    server = PNEUMARelayServer(host=host, port=port, secret_key=secret)
    asyncio.run(server.start())


# ── put ───────────────────────────────────────────────────────
@cli.command()
@click.argument("key")
@click.argument("value")
@click.option("--ttl", default=None, type=int, help="Expiry in seconds")
@click.option("--json", "as_json", is_flag=True, help="Parse value as JSON")
def put(key, value, ttl, as_json):
    """Write a key-value pair to PNEUMA-DB."""
    parsed = json.loads(value) if as_json else value
    result = _api("PUT", "/db", json={"key": key, "value": parsed, "ttl": ttl})
    click.echo(f"✓ {key} written")


# ── get ───────────────────────────────────────────────────────
@cli.command()
@click.argument("key")
@click.option("--raw", is_flag=True, help="Output raw JSON")
def get(key, raw):
    """Read a value from PNEUMA-DB."""
    result = _api("GET", f"/db/{key}")
    if not result["found"]:
        click.echo(f"Key '{key}' not found", err=True)
        sys.exit(1)
    value = result["value"]
    if raw:
        click.echo(json.dumps(value))
    else:
        click.echo(json.dumps(value, indent=2) if isinstance(value, dict) else value)


# ── delete ────────────────────────────────────────────────────
@cli.command()
@click.argument("key")
def delete(key):
    """Delete a key from PNEUMA-DB."""
    _api("DELETE", f"/db/{key}")
    click.echo(f"✓ {key} deleted")


# ── scan ──────────────────────────────────────────────────────
@cli.command()
@click.argument("prefix")
def scan(prefix):
    """Scan all keys starting with PREFIX."""
    result = _api("GET", f"/db/scan/{prefix}")
    click.echo(f"Found {result['count']} keys with prefix '{prefix}':")
    for k, v in result["results"].items():
        v_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        click.echo(f"  {k}: {v_str[:80]}")


# ── stats ─────────────────────────────────────────────────────
@cli.command()
def stats():
    """Show PNEUMA-DB node statistics."""
    result = _api("GET", "/stats")
    click.echo(json.dumps(result, indent=2))


# ── ping ──────────────────────────────────────────────────────
@cli.command()
def ping():
    """Check if PNEUMA-DB server is reachable."""
    result = _api("GET", "/health")
    click.echo(f"✓ PNEUMA-DB reachable — node: {result['node_id']}, version: {result['version']}")


# ── nodes ─────────────────────────────────────────────────────
@cli.command()
def nodes():
    """List all known nodes and their status."""
    result = _api("GET", "/nodes")
    click.echo(f"{'Node ID':<30} {'Reachable':<12} {'Latency':<12}")
    click.echo("-" * 54)
    for node in result["nodes"]:
        latency = f"{node['latency_ms']:.1f}ms" if node.get("latency_ms") else "—"
        status  = "✓ yes" if node["reachable"] else "✗ no"
        click.echo(f"{node['node_id']:<30} {status:<12} {latency:<12}")


def main():
    cli()


if __name__ == "__main__":
    main()
