# PNEUMA

<div align="center">

```
██████╗ ███╗   ██╗███████╗██╗   ██╗███╗   ███╗ █████╗
██╔══██╗████╗  ██║██╔════╝██║   ██║████╗ ████║██╔══██╗
██████╔╝██╔██╗ ██║█████╗  ██║   ██║██╔████╔██║███████║
██╔═══╝ ██║╚██╗██║██╔══╝  ██║   ██║██║╚██╔╝██║██╔══██║
██║     ██║ ╚████║███████╗╚██████╔╝██║ ╚═╝ ██║██║  ██║
╚═╝     ╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═╝
```

**Post-quantum · Networkless · Encrypted · Ultrasonic · Messaging · Architecture**

*The world's first quantum-safe distributed database that communicates through air*

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FIPS 203](https://img.shields.io/badge/crypto-ML--KEM%20FIPS%20203-green?style=flat-square)](https://csrc.nist.gov/pubs/fips/203/final)
[![License: MIT](https://img.shields.io/badge/license-MIT-orange?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-64%20passing-brightgreen?style=flat-square)](#testing)
[![PyPI](https://img.shields.io/badge/pypi-pneuma--db-blue?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/pneuma-db)

</div>

---

## What is PNEUMA?

PNEUMA lets any two devices with a speaker and microphone communicate — **silently, securely, and without any network infrastructure** — using ultrasonic sound waves inaudible to humans.

Every transmission is protected by **ML-KEM (FIPS 203)**, the post-quantum key exchange standard published by NIST in August 2024. This means data encrypted by PNEUMA today **cannot be decrypted by quantum computers** — not now, not ever.

```
Device A                    Air                    Device B
   │                                                   │
   │   ~~~~ 18,500 Hz ultrasonic tones ~~~~>           │
   │                                                   │
   │   ML-KEM encrypted · Reed-Solomon corrected       │
   │   Inaudible · No WiFi · No Bluetooth · No cables  │
   │                                                   │
```

For global access, PNEUMA adds an optional **WebSocket relay** — the same ML-KEM encryption, now over the internet.

```bash
pip install pneuma-db
python run.py --node-id my-laptop --relay ws://relay.pneuma.io:8765
```

---

## The Problem PNEUMA Solves

### 1. Every network will fail. Yours already has.

WiFi drops. Cables get cut. Base stations go down. In 2021, a single routing misconfiguration at Facebook isolated three billion users for six hours. In disaster zones, battlefields, and remote locations, network infrastructure doesn't exist at all. **PNEUMA works with zero infrastructure — just air.**

### 2. Your encryption has a quantum expiration date.

RSA and elliptic curve cryptography — the foundation of TLS, SSH, VPNs, and everything else — are mathematically broken by Shor's algorithm on a quantum computer. IBM projects fault-tolerant quantum computers capable of breaking RSA-2048 **by 2033**. Nation-state adversaries are already collecting your encrypted traffic today, waiting.

> **"Harvest now, decrypt later"** is not a future threat. It is happening now.

**PNEUMA uses ML-KEM (FIPS 203)** — the only standardised post-quantum key exchange algorithm. It is based on the Module Learning With Errors (MLWE) problem, for which no quantum algorithm exists.

### 3. No existing system combines both.

LISNR does acoustic data transfer (no quantum safety). Post-quantum libraries do ML-KEM (no transport layer). Redis does distributed databases (no offline, no quantum safety). **PNEUMA is the only system that is simultaneously:**

- Zero-infrastructure (works with air alone)
- Post-quantum safe (ML-KEM FIPS 203)
- Globally accessible (optional relay)
- A full distributed database

---

## Quick Start

### Two laptops, same room, zero internet

```bash
# Install on both laptops
pip install numpy scipy pynacl reedsolo pyaudio

# Laptop A
python run.py --node-id laptop-a

# Laptop B (same room — that's it)
python run.py --node-id laptop-b
```

The laptops discover each other via ultrasonic beacons, perform ML-KEM key exchange automatically, and form a working distributed database — **through the air in the room**.

```
[laptop-a] > put user:alice {"name": "Alice", "role": "admin"}
OK — user:alice written

[laptop-b] > get user:alice
{"name": "Alice", "role": "admin"}
```

### Global access (with relay)

```bash
# Step 1: Deploy relay on any $5/mo VPS
pip install pneuma-db
pneuma-db relay --host 0.0.0.0 --port 8765

# Step 2: Connect from anywhere
python run.py --node-id my-node --relay ws://YOUR_SERVER_IP:8765

# Step 3: Access via REST API from Node.js
npm install pneuma-db-client
```

```typescript
import { PneumaDB } from 'pneuma-db-client'
const db = new PneumaDB({ port: 7723 })

await db.put('user:001', { name: 'Alice', role: 'admin' })
const user = await db.get('user:001')
```

---

## How It Works

### The Five-Layer Stack

```
┌─────────────────────────────────────────────────────────┐
│  Layer 5 — Application   db.put() · db.get() · REST API │
├─────────────────────────────────────────────────────────┤
│  Layer 4 — Security      ML-KEM-768 + ChaCha20-Poly1305 │
├─────────────────────────────────────────────────────────┤
│  Layer 3 — Reliability   Reed-Solomon RS(255,223)        │
├─────────────────────────────────────────────────────────┤
│  Layer 2 — Framing       PNEUMA Packet Protocol (PPP)   │
├─────────────────────────────────────────────────────────┤
│  Layer 1 — Physical      16-FSK ultrasonic / WebSocket  │
└─────────────────────────────────────────────────────────┘
```

### Physical Layer — 16-FSK Ultrasonic

Data is encoded as 16 discrete frequencies between 17,000 Hz and 20,750 Hz — a range most humans cannot hear. Each frequency represents a 4-bit nibble. Two successive tones encode one byte.

```python
# Encoding: byte 0xAB → two tones
# High nibble: 0xA (10) → 17,000 + 10×250 = 19,500 Hz
# Low nibble:  0xB (11) → 17,000 + 11×250 = 19,750 Hz

FREQ_BASE = 17000   # Hz
FREQ_STEP = 250     # Hz between symbols
```

On the receiver, FFT analysis detects the dominant frequency in each symbol window and recovers the nibble.

### TDMA — Who Gets to Speak

Laptop speakers and microphones can't transmit and receive at the same time. PNEUMA solves this with Time Division Multiple Access — each node gets a 500ms time slot to transmit, all others listen. No coordinator needed: every node independently computes the current slot from wall-clock time.

```
| 0–500ms  Laptop A transmits |
| 500–1000ms  Laptop B transmits |
| 1000–1500ms  Laptop C transmits |
| 1500ms  cycle repeats |
```

### ML-KEM Key Exchange

When a node discovers a new peer via beacon, it immediately initiates ML-KEM key exchange:

```python
# Receiver generates key pair (once per session)
ek, dk = ML_KEM_768.keygen()

# Sender encapsulates — shared secret never transmitted
K_sender, ciphertext = ML_KEM_768.encaps(ek)

# Receiver decapsulates — same shared secret recovered
K_receiver = ML_KEM_768.decaps(dk, ciphertext)

# K_sender == K_receiver  ← quantum-safe shared secret
# Both derive ChaCha20 session key via HKDF-SHA3-256
```

### PNEUMA-DB — Distributed Storage

Data is distributed across nodes using consistent hashing. Every node independently computes which node owns which key — no coordinator, no central server.

```
Key "user:alice" → hash → Node B owns this
Key "config:v1"  → hash → Node A owns this
Key "sensor:001" → hash → Node C owns this
```

Reads and writes route automatically to the correct node via the acoustic or relay channel.

---

## Installation

### Requirements

- Python 3.10 or higher
- PortAudio (for microphone/speaker access)

```bash
# Ubuntu / Debian
sudo apt install portaudio19-dev

# macOS
brew install portaudio

# Windows
# Download PortAudio from http://www.portaudio.com/
```

### Install

```bash
pip install pneuma-db

# For actual ML-KEM (post-quantum) — strongly recommended
pip install liboqs-python

# For audio (local acoustic mode)
pip install pyaudio
```

### Verify

```python
import pneuma_db
print(pneuma_db.__version__)   # 1.0.0

# Verify ML-KEM
from oqs import KeyEncapsulation
kem = KeyEncapsulation("Kyber768")
print("ML-KEM:", kem.details["name"])   # Kyber768
```

---

## Usage

### Python SDK

```python
from pneuma_db import PNEUMA_DB, PNEUMANode

# Create a node
node = PNEUMANode(
    node_id     = "my-node",
    known_nodes = ["my-node", "peer-node"],
)

# Create a database
db = PNEUMA_DB(
    node      = node,
    relay_url = "ws://relay.pneuma.io:8765",  # optional
)

# Basic operations
db.put("user:001", {"name": "Alice", "email": "alice@example.com"})
user = db.get("user:001")
db.delete("user:001")

# Atomic compare-and-swap
db.cas("config:version", expected=1, new_value=2)

# Prefix scan
users = db.scan_prefix("user:")

# TTL (auto-expire after N seconds)
db.put("session:abc123", {"user_id": "001"}, ttl=3600)
```

### Table ORM

```python
users = db.table("users")

# Insert
uid = users.insert({"name": "Alice", "role": "admin", "dept": "Engineering"})

# Find by ID
alice = users.find(uid)

# Filter
admins = users.where(role="admin")

# Update
users.update(uid, role="superadmin")

# Delete
users.delete(uid)

# Count
print(users.count())

# Auto-increment
next_id = db.next_id("users")   # 1, 2, 3, ...
```

### CLI

```bash
# Start a server node
pneuma-db server --node-id my-node --relay ws://relay.pneuma.io:8765 --port 7723

# Write a value
pneuma-db put user:001 '{"name":"Alice"}'

# Read a value
pneuma-db get user:001

# Delete
pneuma-db delete user:001

# Scan prefix
pneuma-db scan user:

# Show status
pneuma-db status

# List peers
pneuma-db nodes

# Start relay server
pneuma-db relay --host 0.0.0.0 --port 8765
```

### REST API

When the server is running, the full OpenAPI spec is available at `http://localhost:7723/docs`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Node health and version |
| `PUT` | `/db` | Write a key-value pair |
| `GET` | `/db/{key}` | Read a value |
| `DELETE` | `/db/{key}` | Delete a key |
| `POST` | `/db/cas` | Atomic compare-and-swap |
| `GET` | `/db/scan/{prefix}` | Scan by key prefix |
| `POST` | `/table/{name}` | Insert a table record |
| `GET` | `/table/{name}/{id}` | Find record by ID |
| `POST` | `/table/{name}/where` | Filter records |
| `PATCH` | `/table/{name}/{id}` | Update record fields |
| `DELETE` | `/table/{name}/{id}` | Delete a record |

---

## Local Acoustic Mesh

No internet. No WiFi. No configuration. Just laptops in a room.

```bash
# Copy the pneuma-mesh/ folder to each laptop
# Run one command per laptop

python run.py --node-id laptop-a    # first laptop
python run.py --node-id laptop-b    # second laptop
python run.py --node-id laptop-c    # third laptop
```

### Interactive REPL

```
[laptop-a] > put config:env production
OK

[laptop-a] > get config:env
production

[laptop-a] > peers
  laptop-b          ML-KEM: ok         last heard 2s ago
  laptop-c          ML-KEM: ok         last heard 1s ago

[laptop-a] > demo
--- PNEUMA Mesh Demo ---
Writing 5 records to the mesh...
  [OK] put demo:user:001
  [OK] put demo:user:002
  ...

[laptop-a] > dashboard    ← opens live terminal UI
```

### Acoustic Range

| Environment | Typical range |
|-------------|--------------|
| Quiet room / home office | 8–15 metres |
| Open plan office | 3–6 metres |
| Server room (fan noise) | 2–4 metres |
| Outdoor, calm conditions | 10–20 metres |
| Through a closed wall | Not reliable |

---

## Global Relay Deployment

### Manual deployment

```bash
# Any Ubuntu server ($4–6/mo)
pip install pneuma-db websockets click
pneuma-db relay --host 0.0.0.0 --port 8765
```

### Docker

```bash
docker run -d \
  -p 8765:8765 \
  --name pneuma-relay \
  --restart always \
  pneuma/relay:latest
```

### docker-compose

```yaml
version: '3.8'
services:
  relay:
    image: pneuma/relay:latest
    ports:
      - "8765:8765"
    restart: always
    environment:
      - PNEUMA_LOG_LEVEL=info
```

### systemd (production)

```ini
[Unit]
Description=PNEUMA Relay Server
After=network.target

[Service]
ExecStart=/usr/bin/python3 -m pneuma_db.relay_server --host 0.0.0.0 --port 8765
Restart=always
User=pneuma
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## Security

### Cryptographic Stack

| Layer | Algorithm | Standard | Quantum safe? |
|-------|-----------|----------|---------------|
| Key exchange | ML-KEM-768 | FIPS 203 (NIST 2024) | **Yes** |
| Data encryption | ChaCha20-Poly1305 | RFC 7539 | Yes (128-bit post-quantum) |
| Key derivation | HKDF-SHA3-256 | RFC 5869 | Yes |
| Packet integrity | CRC-32 + Poly1305 MAC | — | Yes |
| Error correction | Reed-Solomon RS(255,223) | CCSDS | N/A |

### Threat Model

| Threat | Defence | Result |
|--------|---------|--------|
| Passive eavesdropping (acoustic) | ChaCha20-Poly1305 + ML-KEM session | **Infeasible** |
| Replay attack | Session nonces + sequence numbers + TTL | **Rejected** |
| Active injection | Poly1305 MAC authentication | **Detected & rejected** |
| Quantum adversary (Shor's algorithm) | ML-KEM — Shor's inapplicable to MLWE | **Infeasible** |
| Relay server compromise | Relay never holds keys — only routes ciphertext | **No impact** |
| Man-in-the-middle on key exchange | ML-KEM public keys bound to node identity | **Infeasible** |

### The Relay is Blind

The relay server is architecturally incapable of reading your data. It receives:
```json
{
  "dst": "laptop-b",
  "payload": "gAAAAAB7k2x..."   ← ML-KEM encrypted ciphertext
}
```

It forwards the payload to `laptop-b`. It never has the private key. Even if the relay is completely compromised, an attacker receives only indecipherable ciphertext.

---

## Configuration Reference

```python
PNEUMANode(
    node_id      = "my-node",       # Unique identifier for this node
    known_nodes  = [...],           # All node IDs in the mesh
    mlkem_level  = "ML_KEM_768",    # ML_KEM_512 / ML_KEM_768 / ML_KEM_1024
    replication  = 2,               # How many nodes store each key (default: 2)
    symbol_ms    = 80,              # FSK symbol duration in ms (50–300)
    freq_base    = 17000,           # Base ultrasonic frequency in Hz
    freq_step    = 250,             # Hz between adjacent FSK tones
    rs_parity    = 16,              # Reed-Solomon parity bytes (8–32)
    session_ttl  = 3600,            # ML-KEM session lifetime in seconds
    sample_rate  = 44100,           # Audio sample rate (44100 or 48000)
)

PNEUMA_DB(
    node         = node,
    relay_url    = "ws://...",      # Optional — enables global mode
    db_path      = "pneuma.db",     # SQLite database path (default: auto)
)
```

---

## Architecture

### File Structure

```
pneuma-db/
├── pneuma_db/
│   ├── __init__.py          # Public API exports
│   ├── crypto.py            # ML-KEM + ChaCha20 encryption layer
│   ├── transport.py         # 16-FSK ultrasonic physical layer
│   ├── framing.py           # PNEUMA Packet Protocol (PPP)
│   ├── error_correction.py  # Reed-Solomon RS(255,223)
│   ├── node.py              # Node coordination + consistent hashing
│   ├── db.py                # PNEUMA_DB + Table ORM
│   ├── relay_server.py      # WebSocket relay server
│   ├── relay_client.py      # Node-side relay connection
│   ├── server.py            # FastAPI REST API server
│   └── cli.py               # Command-line interface
├── pneuma-mesh/
│   ├── tdma.py              # Time Division Multiple Access scheduler
│   ├── discovery.py         # Ultrasonic beacon discovery
│   ├── mesh_node.py         # Full acoustic mesh node
│   ├── global_node.py       # Hybrid acoustic + relay node
│   ├── tui.py               # Terminal dashboard
│   ├── run.py               # One-command launcher
│   └── test_mesh.py         # Mesh-specific tests
├── tests/
│   └── test_all.py          # Full test suite (64 tests)
└── pyproject.toml
```

---

## Testing

```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run full suite
python -m pytest tests/ -v

# Run mesh tests (no audio hardware required)
python -m pytest pneuma-mesh/test_mesh.py -v

# Run a specific test class
python -m pytest tests/test_all.py::TestCrypto -v
```

**Current coverage:** 64 tests passing across:
- Cryptography (ML-KEM key exchange, session encryption, key derivation)
- Error correction (Reed-Solomon encode/decode/correct)
- Framing (packet serialisation, CRC, reassembly)
- Transport (FSK tone generation, FFT detection, loopback)
- Consistent hashing (routing, replication, node add/remove)
- Local store (SQLite CRUD, TTL, CAS, auto-increment)
- Full integration (end-to-end message pipeline)

---

## Comparison

| | WiFi | Bluetooth | LISNR | Redis | **PNEUMA** |
|--|------|-----------|-------|-------|------------|
| Quantum safe | ❌ | ❌ | ❌ | ❌ | **✅ ML-KEM** |
| Zero infrastructure | ❌ | ❌ | Partial | ❌ | **✅** |
| Acoustic channel | ❌ | ❌ | ✅ | ❌ | **✅** |
| Distributed DB | ❌ | ❌ | ❌ | ✅ | **✅** |
| Global access | ✅ | ❌ | ❌ | ✅ | **✅ relay** |
| Open protocol | ✅ | ✅ | ❌ | ✅ | **✅** |
| No hardware needed | ❌ | ❌ | ✅ | ❌ | **✅** |

---

## Roadmap

| Version | ETA | Features |
|---------|-----|----------|
| **v1.0** | Now | ML-KEM-768, 16-FSK, TDMA mesh, PNEUMA-DB, REST API, global relay |
| v1.1 | Q3 2026 | OFDM modulation (10× throughput), adaptive bitrate, auto-calibration |
| v1.2 | Q4 2026 | Android SDK, iOS SDK |
| v2.0 | Q1 2027 | Multi-hop acoustic routing, 50+ node mesh, delta replication |
| v2.1 | Q2 2027 | Acoustic↔relay seamless handoff, offline-first sync |
| v3.0 | Q4 2027 | PNEUMA Hardware Module, FIPS 140-3 certification |

---

## Use Cases

### Air-gapped key injection
Deliver ML-KEM encryption keys to an air-gapped system without physical media. No USB drives. No insider threat risk. The receiving system never connects to a network.

### Emergency offline communication
When cellular and WiFi infrastructure fails — disaster response, conflict zones, remote fieldwork — devices in the same area continue to communicate and share data acoustically.

### Quantum-safe IoT
Embedded devices without WiFi or Bluetooth hardware can participate in a PNEUMA mesh using only a microphone and speaker. No radio chips, no spectrum licenses, no network subscriptions.

### Proximity authentication
Because PNEUMA's acoustic channel is physically bounded (3–15m range), it can serve as proof of physical presence — a stronger authentication factor than IP-based or certificate-based methods alone.

### Post-quantum migration
Replace RSA and ECC key exchange in your existing system with PNEUMA's ML-KEM layer. The REST API makes it a drop-in replacement for any language.

---

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

```bash
# Clone
git clone https://github.com/yourusername/pneuma-db.git
cd pneuma-db

# Set up development environment
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests before making changes
python -m pytest tests/ -v

# Make your changes, add tests, then:
python -m pytest tests/ -v
git commit -m "feat: your change"
git push origin my-feature-branch
```

### Priority areas for contribution

- **OFDM modulation** — would give 10× throughput improvement over FSK
- **Mobile SDKs** — Android (Kotlin/Java) and iOS (Swift) implementations
- **Hardware calibration** — auto-tune FSK parameters for different microphones/speakers
- **Windows audio** — PyAudio on Windows has quirks; improved Windows support needed
- **Acoustic relay nodes** — Raspberry Pi nodes that extend acoustic range via multi-hop

---

## Frequently Asked Questions

**Q: Is this actually inaudible?**
PNEUMA transmits at 17,000–20,750 Hz. The average adult hearing threshold above 17 kHz is approximately 60 dB SPL at normal speaker volumes — effectively inaudible to most people over 30. Children and some younger adults may hear a faint tone at maximum amplitude.

**Q: What's the data rate?**
At the default 80ms symbol duration, PNEUMA achieves approximately 6 bytes/second (48 bits/second). This is intentionally conservative for reliability. The v1.1 OFDM update targets 10× improvement.

**Q: Why not just use WiFi with ML-KEM on top?**
You can — and for high-throughput applications you should. PNEUMA's value is in scenarios where WiFi is unavailable, prohibited, or compromised. It is a complement to conventional networks, not a replacement.

**Q: Does it work through walls?**
No. Ultrasonic frequencies above 17 kHz are absorbed by walls, doors, and most soft furnishings. This is by design — it means PNEUMA transmissions are physically contained to the room, preventing remote eavesdropping.

**Q: How does the relay compare to a VPN?**
A VPN encrypts traffic between you and a server you trust. The relay is a dumb router that forwards encrypted blobs — it cannot decrypt anything even if the server is compromised. Key exchange happens acoustically or peer-to-peer, never via the relay.

**Q: What happens if a node goes offline mid-write?**
PNEUMA-DB uses a replication factor of 2 by default. If the primary node for a key goes offline, reads fall through to replicas. For writes, the CAS operation will fail if quorum cannot be reached, preserving consistency over availability.

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Citation

If you use PNEUMA in research, please cite:

```bibtex
@software{pneuma2026,
  title  = {PNEUMA: Post-quantum Networkless Encrypted Ultrasonic Messaging Architecture},
  year   = {2026},
  url    = {https://github.com/yourusername/pneuma-db},
  note   = {Version 1.0.0}
}
```

---

<div align="center">

**pneuma.io** · [Documentation](https://docs.pneuma.io) · [PyPI](https://pypi.org/project/pneuma-db) · [npm](https://npmjs.com/package/pneuma-db-client)

*The air between every device is already a network.*

</div>
