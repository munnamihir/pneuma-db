# Installing PNEUMA

## Quick install (all platforms)

```bash
bash setup.sh
```

---

## Platform-specific instructions

### Ubuntu / Debian / WSL2 (Windows Subsystem for Linux)

```bash
# System packages
sudo apt update
sudo apt install -y python3 python3-pip python3-venv portaudio19-dev git

# Clone the repo
git clone https://github.com/YOUR_USERNAME/pneuma-db.git
cd pneuma-db

# Run setup
bash setup.sh
```

### macOS

```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# System packages
brew install python@3.12 portaudio git

# Clone
git clone https://github.com/YOUR_USERNAME/pneuma-db.git
cd pneuma-db

# Run setup
bash setup.sh
```

### Windows (native — not WSL)

```powershell
# Install Python from python.org (check "Add to PATH")
# Install Git from git-scm.com
# Install PortAudio: winget install PortAudio.PortAudio

# Clone
git clone https://github.com/YOUR_USERNAME/pneuma-db.git
cd pneuma-db

# Install dependencies manually
pip install numpy scipy pynacl reedsolo fastapi uvicorn websockets click requests pydantic sqlparse

# Install pyaudio on Windows
pip install pipwin
pipwin install pyaudio
```

### Raspberry Pi (acoustic IoT node)

```bash
sudo apt update
sudo apt install -y python3 python3-pip portaudio19-dev git python3-pyaudio

git clone https://github.com/YOUR_USERNAME/pneuma-db.git
cd pneuma-db
pip3 install -r requirements-relay.txt
pip3 install numpy scipy pynacl reedsolo

# Run as mesh node
python3 pneuma_mesh/run.py --node-id pi-node-1
```

---

## Verify installation

```bash
# Check everything is working
python -c "
import pneuma_db
print('PNEUMA-DB:', pneuma_db.__version__)

try:
    import oqs
    print('ML-KEM (FIPS 203): installed — fully quantum-safe')
except ImportError:
    print('ML-KEM: not found — using X25519 fallback (install liboqs for quantum safety)')

try:
    import pyaudio
    print('Audio (PyAudio): installed — acoustic mode available')
except ImportError:
    print('Audio: not installed — relay-only mode')
"
```

---

## Run tests

```bash
python -m pytest tests/ -v                      # 64 core tests
python -m pytest tests/test_sql.py -v           # 59 SQL tests  
python -m pytest tests/test_mesh.py -v          # 24 mesh tests
```

All tests should pass without audio hardware.
