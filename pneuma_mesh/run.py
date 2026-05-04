"""
PNEUMA Acoustic Mesh — One-command launcher
============================================

Usage:
    # Laptop 1
    python run.py --node-id laptop-a

    # Laptop 2 (same room)
    python run.py --node-id laptop-b

    # Laptop 3 (same room)
    python run.py --node-id laptop-c

That's it. No config. No server. No internet.
The laptops discover each other via ultrasonic beacons.
ML-KEM sessions establish automatically.
The DB distributes across all nodes.

Interactive commands (after startup):
    put <key> <value>        — store a value
    get <key>                — retrieve a value
    delete <key>             — delete a key
    scan <prefix>            — list all keys with prefix
    status                   — show node status
    peers                    — list active peers
    demo                     — run a quick demo
    dashboard                — open live terminal dashboard
    quit                     — exit
"""

import sys
import time
import json
import threading
import argparse


def main():
    parser = argparse.ArgumentParser(description="PNEUMA Acoustic Mesh Node")
    parser.add_argument("--node-id",   required=True,  help="Unique name for this laptop")
    parser.add_argument("--slot-ms",   default=500,    type=int, help="TDMA slot duration (ms)")
    parser.add_argument("--symbol-ms", default=80,     type=int, help="FSK symbol duration (ms)")
    parser.add_argument("--dashboard", action="store_true",       help="Open terminal dashboard")
    parser.add_argument("--db-path",   default=None,              help="SQLite DB path")
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════════╗
║           PNEUMA Acoustic Mesh Node                  ║
║     No internet  ·  No WiFi  ·  Pure air             ║
║                                                      ║
║  Node ID : {args.node_id:<40}║
║  TDMA    : {args.slot_ms}ms slots                              ║
║  Crypto  : ML-KEM (post-quantum)                     ║
╚══════════════════════════════════════════════════════╝
""")

    from mesh_node import AcousticMeshNode
    node = AcousticMeshNode(
        node_id   = args.node_id,
        slot_ms   = args.slot_ms,
        symbol_ms = args.symbol_ms,
        db_path   = args.db_path,
    )
    node.start()

    if args.dashboard:
        from tui import run_dashboard
        threading.Thread(target=run_dashboard, args=(node,), daemon=True).start()

    print("\nNode started. Type 'help' for commands.\n")
    _repl(node)


def _repl(node):
    """Simple interactive REPL for the mesh node."""
    while True:
        try:
            line = input(f"[{node.node_id}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nStopping node...")
            node.stop()
            break

        if not line:
            continue

        parts = line.split(maxsplit=2)
        cmd   = parts[0].lower()

        if cmd in ("quit", "exit", "q"):
            node.stop()
            break

        elif cmd == "help":
            print("""
Commands:
  put <key> <value>     Store a value (JSON or string)
  get <key>             Retrieve a value
  delete <key>          Delete a key
  scan <prefix>         List keys with prefix
  status                Show node status
  peers                 List active peers
  demo                  Run a quick demonstration
  dashboard             Open live terminal dashboard
  quit                  Exit
""")

        elif cmd == "put" and len(parts) >= 3:
            key, val = parts[1], parts[2]
            try:
                val = json.loads(val)
            except Exception:
                pass
            ok = node.db_put(key, val)
            print(f"{'OK' if ok else 'FAILED'} — {key} = {val}")

        elif cmd == "get" and len(parts) >= 2:
            val = node.db_get(parts[1])
            if val is None:
                print(f"Key '{parts[1]}' not found")
            else:
                print(json.dumps(val, indent=2) if isinstance(val, dict) else val)

        elif cmd == "delete" and len(parts) >= 2:
            node.db_delete(parts[1])
            print(f"Deleted: {parts[1]}")

        elif cmd == "scan" and len(parts) >= 2:
            results = node.db_scan(parts[1])
            if not results:
                print(f"No keys with prefix '{parts[1]}'")
            else:
                for k, v in results.items():
                    vs = json.dumps(v) if isinstance(v, dict) else str(v)
                    print(f"  {k}: {vs[:60]}")

        elif cmd == "status":
            s = node.status()
            print(json.dumps(s, indent=2))

        elif cmd == "peers":
            peers = node.discovery.active_peers()
            if not peers:
                print("No peers discovered yet — make sure other laptops are running")
            else:
                for p in peers:
                    ts  = node.discovery.peer_last_seen(p)
                    ago = f"{time.time() - ts:.0f}s ago" if ts else "?"
                    has_session = p in node._sessions
                    print(f"  {p:<20} ML-KEM={'ok' if has_session else 'pending':<10} last heard {ago}")

        elif cmd == "demo":
            _run_demo(node)

        elif cmd == "dashboard":
            from tui import run_dashboard
            run_dashboard(node)

        else:
            print(f"Unknown command: {cmd}. Type 'help' for commands.")


def _run_demo(node):
    print("\n--- PNEUMA Mesh Demo ---")
    print("Writing 5 records to the mesh...")

    records = [
        ("demo:user:001", {"name": "Alice",   "role": "admin",  "dept": "Engineering"}),
        ("demo:user:002", {"name": "Bob",     "role": "editor", "dept": "Design"}),
        ("demo:config:version", "1.0.0"),
        ("demo:config:mode",    "offline-acoustic"),
        ("demo:sensor:temp",    {"celsius": 22.5, "unit": "C"}),
    ]

    for key, val in records:
        ok = node.db_put(key, val)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] put {key}")
        time.sleep(0.3)

    print("\nReading records back...")
    for key, _ in records:
        val = node.db_get(key)
        vs  = json.dumps(val) if isinstance(val, dict) else str(val)
        print(f"  get {key} → {vs[:50]}")
        time.sleep(0.1)

    print("\nScanning 'demo:user:' prefix...")
    users = node.db_scan("demo:user:")
    for k, v in users.items():
        print(f"  {k}: {v.get('name', v)}")

    print("\nDemo complete!")
    print(f"Total local records: {node.store.count()}")
    print(f"Active peers: {node.discovery.active_peers()}")
    print("─" * 40)


if __name__ == "__main__":
    main()
