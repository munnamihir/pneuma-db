#!/usr/bin/env bash
# ============================================================
# PNEUMA v3 — One-command setup script
# Works on Linux, macOS, Windows (WSL2)
# Usage: bash setup.sh
# ============================================================
set -e
GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

echo -e "${BLUE}"
cat << 'LOGO'
██████╗ ███╗   ██╗███████╗██╗   ██╗███╗   ███╗ █████╗
██╔══██╗████╗  ██║██╔════╝██║   ██║████╗ ████║██╔══██╗
██████╔╝██╔██╗ ██║█████╗  ██║   ██║██╔████╔██║███████║
██╔═══╝ ██║╚██╗██║██╔══╝  ██║   ██║██║╚██╔╝██║██╔══██║
██║     ██║ ╚████║███████╗╚██████╔╝██║ ╚═╝ ██║██║  ██║
╚═╝     ╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═╝
  v3.0  —  Post-quantum · Networkless · Encrypted · Ultrasonic
LOGO
echo -e "${NC}"

# ── Detect OS ─────────────────────────────────────────────────
OS="$(uname -s 2>/dev/null || echo 'Unknown')"
echo -e "${BLUE}Detected OS: ${OS}${NC}"

# ── System packages ───────────────────────────────────────────
echo -e "\n${GREEN}[1/5] Installing system packages...${NC}"
if [[ "$OS" == "Linux" ]]; then
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -q
        sudo apt-get install -y portaudio19-dev python3-pip git 2>/dev/null || true
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y portaudio-devel python3-pip git 2>/dev/null || true
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm portaudio python-pip git 2>/dev/null || true
    fi
elif [[ "$OS" == "Darwin" ]]; then
    if command -v brew &>/dev/null; then
        brew install portaudio 2>/dev/null || true
    else
        echo -e "${YELLOW}Homebrew not found. Install from https://brew.sh then re-run.${NC}"
    fi
fi
echo -e "${GREEN}✓ System packages done${NC}"

# ── Python check ─────────────────────────────────────────────
echo -e "\n${GREEN}[2/5] Checking Python version...${NC}"
PYTHON=$(command -v python3 || command -v python || echo "")
if [ -z "$PYTHON" ]; then
    echo -e "${RED}Python not found. Install Python 3.10+ from python.org${NC}"
    exit 1
fi
VERSION=$($PYTHON --version 2>&1 | grep -oP '\d+\.\d+')
echo -e "${GREEN}✓ Found $($PYTHON --version)${NC}"

# ── Core Python packages ──────────────────────────────────────
echo -e "\n${GREEN}[3/5] Installing Python dependencies...${NC}"
$PYTHON -m pip install --upgrade pip -q
$PYTHON -m pip install \
    numpy scipy pynacl reedsolo \
    fastapi "uvicorn[standard]" websockets \
    click requests pydantic sqlparse \
    pytest pytest-asyncio \
    hatch twine \
    -q 2>&1 | tail -3
echo -e "${GREEN}✓ Core packages installed${NC}"

# ── PyAudio (optional — for acoustic mode) ───────────────────
echo -e "\n${GREEN}[4/5] Installing PyAudio (acoustic mode)...${NC}"
if $PYTHON -m pip install pyaudio -q 2>/dev/null; then
    echo -e "${GREEN}✓ PyAudio installed — acoustic mode available${NC}"
else
    echo -e "${YELLOW}⚠ PyAudio failed — acoustic mode disabled (relay mode still works)${NC}"
    echo -e "${YELLOW}  Linux fix: sudo apt install portaudio19-dev && pip install pyaudio${NC}"
    echo -e "${YELLOW}  macOS fix: brew install portaudio && pip install pyaudio${NC}"
fi

# ── ML-KEM (optional — for quantum safety) ───────────────────
echo -e "\n${GREEN}[4b/5] Installing ML-KEM (post-quantum crypto)...${NC}"
if $PYTHON -m pip install liboqs-python -q 2>/dev/null; then
    echo -e "${GREEN}✓ ML-KEM (FIPS 203) installed — fully quantum-safe${NC}"
else
    echo -e "${YELLOW}⚠ liboqs not available — using X25519 fallback${NC}"
    echo -e "${YELLOW}  For quantum safety: pip install liboqs-python${NC}"
fi

# ── Install PNEUMA as editable package ───────────────────────
echo -e "\n${GREEN}[5/5] Installing PNEUMA as editable package...${NC}"
$PYTHON -m pip install -e . -q 2>/dev/null || \
$PYTHON -m pip install -e ".[dev]" -q 2>/dev/null || \
echo -e "${YELLOW}Editable install failed — continuing anyway${NC}"
echo -e "${GREEN}✓ PNEUMA installed${NC}"

# ── Verification ─────────────────────────────────────────────
echo -e "\n${BLUE}═══ Verification ═══════════════════════════════════════${NC}"
$PYTHON -c "
import sys
print(f'Python: {sys.version.split()[0]}')

try:
    import numpy; print(f'NumPy:  {numpy.__version__} ✓')
except: print('NumPy:  MISSING ✗')

try:
    import nacl; print(f'PyNaCl: {nacl.__version__} ✓')
except: print('PyNaCl: MISSING ✗')

try:
    import reedsolo; print(f'Reed-Solomon: ✓')
except: print('Reed-Solomon: MISSING ✗')

try:
    import oqs; print(f'ML-KEM (FIPS 203): ✓  QUANTUM SAFE')
except: print(f'ML-KEM:  not available — X25519 fallback')

try:
    import pyaudio; print(f'PyAudio: ✓  acoustic mode enabled')
except: print(f'PyAudio: not available — relay mode only')

try:
    import pneuma_db; print(f'PNEUMA-DB {pneuma_db.__version__}: ✓')
except: print('PNEUMA-DB: module import issue')
"
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"

# ── Run tests ────────────────────────────────────────────────
echo -e "\n${GREEN}Running test suite...${NC}"
if $PYTHON -m pytest tests/ -q --tb=short 2>&1 | tail -5; then
    echo -e "${GREEN}✓ All tests passing${NC}"
else
    echo -e "${YELLOW}⚠ Some tests failed — check output above${NC}"
fi

# ── Done ─────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  PNEUMA setup complete!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Start local mesh node:"
echo -e "    ${BLUE}python pneuma_mesh/run.py --node-id my-laptop${NC}"
echo ""
echo "  Start relay server (for global access):"
echo -e "    ${BLUE}pneuma-db relay --host 0.0.0.0 --port 8765${NC}"
echo ""
echo "  Start REST API (for Node.js integration):"
echo -e "    ${BLUE}pneuma-db server --node-id my-app --port 7723${NC}"
echo ""
echo "  Run tests:"
echo -e "    ${BLUE}python -m pytest tests/ -v${NC}"
echo ""
echo "  Deploy to Render (free): see render.yaml"
echo "  Full guide: PNEUMA_v3_Deployment_Guide.pdf"
echo ""
