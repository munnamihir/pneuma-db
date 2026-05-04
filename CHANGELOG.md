# Changelog

All notable changes to PNEUMA are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [3.0.0] — 2026

### Added
- **pneuma_sql** — Full SQL layer (DB-API 2.0) on top of PNEUMA-DB
  - CREATE TABLE, INSERT, SELECT, UPDATE, DELETE, DROP TABLE
  - WHERE with =, >, <, >=, <=, <>, AND, OR, LIKE, IS NULL, IN
  - ORDER BY, LIMIT, OFFSET
  - PRIMARY KEY, UNIQUE, NOT NULL, DEFAULT, AUTOINCREMENT
  - CREATE INDEX
  - PRAGMA table_info
  - Type coercion: INTEGER, REAL, TEXT, BOOLEAN, BLOB
  - `pneuma_sql.connect()` — drop-in sqlite3 replacement
- **Global relay mode** — Access PNEUMA-DB from anywhere via WebSocket relay
- **Hybrid mode** — Acoustic for local peers, relay for remote
- **GlobalMeshNode** — Extends acoustic mesh with relay connectivity
- **REST API** — Full HTTP API for Node.js/TypeScript/browser integration
- **pneuma-db-client** — TypeScript/npm package for web integration
- **Docker support** — Official Dockerfile for relay deployment
- **Render/Railway configs** — One-click free deployment
- **GitHub Actions** — CI on Linux/macOS/Windows, auto-publish to PyPI

### Changed
- ML-KEM key derivation now uses deterministic salt (sha256 of shared secret)
  ensuring both sides produce identical session keys
- TDMA scheduler now handles clock drift more gracefully
- Reed-Solomon parity configurable (8–32 bytes, default 16)

### Fixed
- LIKE pattern matching now correctly handles `%`, `_`, and regex special chars
- X25519 fallback key exchange session keys now match between sender/receiver
- PyAudio stream cleanup on exception in transport layer

---

## [2.0.0] — 2026

### Added
- **TDMA acoustic mesh** — time-division multiple access for multi-device rooms
- **Node discovery** — ultrasonic beacon protocol with transitive peer discovery
- **AcousticMeshNode** — full offline mesh coordinator
- **Terminal dashboard** — live curses UI showing mesh status
- **PNEUMA-DB REST API** — FastAPI server on port 7723

### Changed
- Transport layer now uses Hann windowing on all FSK tones (eliminates spectral leakage)
- Framing layer adds per-packet CRC-32 and PNMA magic header

---

## [1.0.0] — 2026

### Added
- **16-FSK ultrasonic physical layer** — 17,000–20,750 Hz, 100ms symbols
- **ML-KEM-768 key exchange** (FIPS 203) with X25519 fallback
- **ChaCha20-Poly1305 data encryption** via libsodium/pynacl
- **Reed-Solomon error correction** — RS(255,223), corrects up to 16 byte errors
- **PNEUMA Packet Protocol** — framing with CRC-32, sequencing, fragmentation
- **PNEUMA-DB** — distributed key-value store with consistent hashing
- **SQLite per-node local store** — disk-backed, TTL support, auto-increment
- **Table ORM** — insert, find, where, update, delete, count
- **WebSocket relay server** — stateless, blind router
- Initial Python SDK and CLI
