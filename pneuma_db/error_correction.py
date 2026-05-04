"""
PNEUMA Error Correction Layer — Layer 3
========================================
Reed-Solomon RS(255,223) — same configuration as NASA deep-space
comms and audio CD error correction. Corrects up to 16 byte errors
per 223-byte codeword, handles acoustic burst noise well.

Dependency: reedsolo
"""

import reedsolo


# RS(255, 223) → 32 parity bytes → corrects up to 16 byte errors
RS_PARITY_BYTES = 16   # configurable — more = slower but more robust


class ErrorCorrection:
    """
    Wraps reedsolo for PNEUMA use.

    Usage:
        ec = ErrorCorrection(parity=16)
        encoded = ec.encode(b"my data")
        decoded = ec.decode(encoded)   # tolerates up to 16 byte errors
    """

    def __init__(self, parity: int = RS_PARITY_BYTES):
        self.parity  = parity
        self._codec  = reedsolo.RSCodec(parity)

    def encode(self, data: bytes) -> bytes:
        """Encode data with Reed-Solomon parity bytes appended."""
        return bytes(self._codec.encode(data))

    def decode(self, data: bytes) -> tuple[bytes, int]:
        """
        Decode and correct errors.
        Returns (corrected_data, num_errors_corrected).
        Raises reedsolo.ReedSolomonError if uncorrectable.
        """
        decoded_msg, _, errata_pos = self._codec.decode(data)
        return bytes(decoded_msg), len(errata_pos)

    def decode_safe(self, data: bytes) -> tuple[bytes | None, int]:
        """
        Like decode() but returns (None, -1) instead of raising
        if the data is uncorrectable.
        """
        try:
            msg, n_errors = self.decode(data)
            return msg, n_errors
        except reedsolo.ReedSolomonError:
            return None, -1

    @property
    def overhead_bytes(self) -> int:
        """Number of parity bytes added per encode."""
        return self.parity

    def max_correctable_errors(self) -> int:
        return self.parity // 2
