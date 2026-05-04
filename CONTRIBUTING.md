# Contributing to PNEUMA

Thank you for your interest in contributing! PNEUMA is an open-source, MIT-licensed project.

## Ways to contribute

- **Bug reports** — open an issue with the error message and your OS/Python version
- **Bug fixes** — fork, fix, test, pull request
- **New features** — open an issue first to discuss before implementing
- **Documentation** — improve README, add examples, fix typos
- **Testing** — add test cases for edge cases you find

## Priority areas

These are the highest-impact contributions:

1. **OFDM modulation** — replaces 16-FSK with orthogonal multi-carrier for 10× throughput
2. **Windows audio support** — PyAudio on Windows has quirks, robust Windows support needed
3. **Mobile (Android/iOS)** — Python or native Swift/Kotlin port of the transport layer
4. **Acoustic relay nodes** — Raspberry Pi nodes that extend acoustic range via multi-hop
5. **Hardware calibration** — auto-tune FSK parameters for different microphone/speaker hardware

## Development setup

```bash
git clone https://github.com/YOUR_USERNAME/pneuma-db.git
cd pneuma-db
pip install -e ".[dev]"
```

## Before submitting a pull request

```bash
# Run all tests
python -m pytest tests/ -v

# Run SQL tests
python -m pytest tests/test_sql.py -v

# Run mesh tests
python -m pytest tests/test_mesh.py -v

# Lint (optional but appreciated)
ruff check pneuma_db pneuma_sql pneuma_mesh --ignore E501
```

All tests must pass. Add new tests for new features.

## Code style

- Python 3.10+ syntax (use `X | Y` unions, `match` statements where natural)
- Type hints on all public functions
- Docstrings on all classes and public methods
- Line length 100 max (not strictly enforced)

## Commit message format

```
type: short description

Longer explanation if needed.
```

Types: `feat`, `fix`, `docs`, `test`, `chore`, `refactor`

## License

By contributing, you agree your contributions are licensed under the MIT License.
