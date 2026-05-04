"""
PNEUMA Transport Layer — Physical Layer (Layer 1)
=================================================
16-FSK ultrasonic modulation over the 17,000–20,750 Hz band.
Each byte is split into two 4-bit nibbles → two tones.
FFT-based detection on the receiver side.

Dependencies: numpy, scipy, pyaudio
"""

import threading
import queue
import time
import struct
from typing import Optional, Callable

import numpy as np

try:
    import pyaudio
    _HAS_AUDIO = True
except ImportError:
    _HAS_AUDIO = False
    print("[PNEUMA] pyaudio not found — audio I/O disabled. Install: pip install pyaudio")

# ── FSK Parameters ───────────────────────────────────────────
SAMPLE_RATE    = 44100      # Hz — universal standard
SYMBOL_MS      = 100        # ms per tone symbol (configurable)
FREQ_BASE      = 17000      # Hz — lowest FSK frequency
FREQ_STEP      = 250        # Hz between adjacent symbols
NUM_SYMBOLS    = 16         # 4 bits per symbol
AMPLITUDE      = 0.45       # 0.0–1.0 speaker amplitude
FFT_SIZE       = 4096       # FFT window size

# Derive all 16 frequencies
FREQUENCIES = [FREQ_BASE + i * FREQ_STEP for i in range(NUM_SYMBOLS)]
# [17000, 17250, 17500, 17750, 18000, 18250, 18500, 18750,
#  19000, 19250, 19500, 19750, 20000, 20250, 20500, 20750]

# Preamble: 8 alternating 0x0/0xF nibbles for frame sync
PREAMBLE_NIBBLES = [0x0, 0xF] * 4
PREAMBLE_BYTES   = bytes([0x0F] * 4)   # as bytes for framing layer


class PNEUMATransport:
    """
    Handles ultrasonic FSK modulation and demodulation.

    Usage:
        tx = PNEUMATransport(symbol_ms=100)
        tx.transmit(b"hello")          # plays through speaker

        rx = PNEUMATransport(symbol_ms=100)
        data = rx.receive(num_bytes=5) # listens via microphone
    """

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        symbol_ms:   int = SYMBOL_MS,
        freq_base:   int = FREQ_BASE,
        freq_step:   int = FREQ_STEP,
        amplitude:  float = AMPLITUDE,
    ):
        self.sample_rate = sample_rate
        self.symbol_ms   = symbol_ms
        self.freq_base   = freq_base
        self.freq_step   = freq_step
        self.amplitude   = amplitude
        self.frequencies = [freq_base + i * freq_step for i in range(NUM_SYMBOLS)]
        self.samples_per_symbol = int(sample_rate * symbol_ms / 1000)

    # ── Tone synthesis ────────────────────────────────────────
    def _generate_tone(self, freq: float) -> np.ndarray:
        """Generate one FSK symbol tone with Hann windowing."""
        n     = self.samples_per_symbol
        t     = np.linspace(0, self.symbol_ms / 1000, n, endpoint=False)
        tone  = self.amplitude * np.sin(2 * np.pi * freq * t)
        window = np.hanning(n)
        return (tone * window).astype(np.float32)

    def _nibble_to_tone(self, nibble: int) -> np.ndarray:
        """Convert 4-bit nibble (0–15) to a tone buffer."""
        return self._generate_tone(self.frequencies[nibble & 0x0F])

    def _byte_to_tones(self, byte_val: int) -> tuple[np.ndarray, np.ndarray]:
        """Split byte into two nibbles → two tones."""
        high = (byte_val >> 4) & 0x0F
        low  = byte_val & 0x0F
        return self._nibble_to_tone(high), self._nibble_to_tone(low)

    # ── FFT frequency detection ───────────────────────────────
    def _detect_nibble(self, samples: np.ndarray) -> int:
        """
        Detect the dominant FSK frequency in a sample window.
        Returns the nibble value (0–15) corresponding to that frequency.
        """
        windowed = samples * np.hanning(len(samples))
        fft_mag  = np.abs(np.fft.rfft(windowed, n=FFT_SIZE))
        freqs    = np.fft.rfftfreq(FFT_SIZE, 1.0 / self.sample_rate)

        # Restrict search to our FSK band + 100 Hz margin
        band_lo = self.freq_base - 100
        band_hi = self.freq_base + NUM_SYMBOLS * self.freq_step + 100
        mask     = (freqs >= band_lo) & (freqs <= band_hi)

        if not np.any(mask):
            return 0

        masked_fft = fft_mag.copy()
        masked_fft[~mask] = 0
        peak_freq = freqs[np.argmax(masked_fft)]

        # Map peak frequency to nearest nibble
        nibble = round((peak_freq - self.freq_base) / self.freq_step)
        return int(max(0, min(15, nibble)))

    # ── Transmit ─────────────────────────────────────────────
    def transmit(self, data: bytes, include_preamble: bool = True) -> None:
        """Play data as ultrasonic tones through the speaker."""
        if not _HAS_AUDIO:
            raise RuntimeError("pyaudio not installed — cannot transmit")

        p      = pyaudio.PyAudio()
        stream = p.open(
            format   = pyaudio.paFloat32,
            channels = 1,
            rate     = self.sample_rate,
            output   = True,
            frames_per_buffer = self.samples_per_symbol * 2,
        )

        try:
            # Preamble for frame synchronization
            if include_preamble:
                for nibble in PREAMBLE_NIBBLES:
                    stream.write(self._nibble_to_tone(nibble).tobytes())

            # Data bytes
            for byte_val in data:
                high_tone, low_tone = self._byte_to_tones(byte_val)
                stream.write(high_tone.tobytes())
                stream.write(low_tone.tobytes())

        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

    def transmit_async(self, data: bytes, callback: Optional[Callable] = None) -> threading.Thread:
        """Non-blocking transmit. Calls callback(None) when done."""
        def _run():
            self.transmit(data)
            if callback:
                callback(None)
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

    # ── Receive ──────────────────────────────────────────────
    def receive(
        self,
        num_bytes: int,
        timeout:   float = 30.0,
        wait_for_preamble: bool = True,
    ) -> Optional[bytes]:
        """
        Listen via microphone and decode ultrasonic tones.
        Returns decoded bytes, or None on timeout.
        """
        if not _HAS_AUDIO:
            raise RuntimeError("pyaudio not installed — cannot receive")

        p      = pyaudio.PyAudio()
        stream = p.open(
            format   = pyaudio.paFloat32,
            channels = 1,
            rate     = self.sample_rate,
            input    = True,
            frames_per_buffer = self.samples_per_symbol,
        )

        result    = []
        deadline  = time.time() + timeout

        try:
            if wait_for_preamble:
                if not self._wait_for_preamble(stream, deadline):
                    return None   # timed out waiting for preamble

            # Read num_bytes × 2 symbols
            for _ in range(num_bytes):
                if time.time() > deadline:
                    return None

                raw_high  = stream.read(self.samples_per_symbol, exception_on_overflow=False)
                raw_low   = stream.read(self.samples_per_symbol, exception_on_overflow=False)

                samples_high = np.frombuffer(raw_high, dtype=np.float32)
                samples_low  = np.frombuffer(raw_low,  dtype=np.float32)

                high_nibble = self._detect_nibble(samples_high)
                low_nibble  = self._detect_nibble(samples_low)

                result.append((high_nibble << 4) | low_nibble)

            return bytes(result)

        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

    def _wait_for_preamble(self, stream, deadline: float) -> bool:
        """
        Block until preamble is detected in the audio stream.
        Returns True if preamble found, False if timed out.
        """
        preamble_target = [0, 15, 0, 15, 0, 15, 0, 15]
        window = []

        while time.time() < deadline:
            raw = stream.read(self.samples_per_symbol, exception_on_overflow=False)
            samples = np.frombuffer(raw, dtype=np.float32)
            nibble  = self._detect_nibble(samples)
            window.append(nibble)

            if len(window) > len(preamble_target):
                window.pop(0)

            if window == preamble_target:
                return True

        return False

    # ── Continuous listener (background thread) ───────────────
    def start_listener(
        self,
        on_packet: Callable[[bytes], None],
        packet_size: int = 256,
    ) -> threading.Event:
        """
        Start a background thread that continuously listens
        and calls on_packet(bytes) whenever a packet is detected.
        Returns a stop_event — call stop_event.set() to stop.
        """
        stop_event = threading.Event()

        def _listen():
            while not stop_event.is_set():
                try:
                    data = self.receive(
                        num_bytes=packet_size,
                        timeout=5.0,
                        wait_for_preamble=True,
                    )
                    if data:
                        on_packet(data)
                except Exception as e:
                    if not stop_event.is_set():
                        print(f"[PNEUMA listener error] {e}")

        t = threading.Thread(target=_listen, daemon=True)
        t.start()
        return stop_event

    # ── Calibration ───────────────────────────────────────────
    def calibrate(self, test_freq: float = 18500.0, duration: float = 1.0) -> dict:
        """
        Play a test tone and measure round-trip to verify the
        acoustic channel is working. Returns calibration results.
        """
        if not _HAS_AUDIO:
            return {"ok": False, "error": "pyaudio not installed"}

        # Generate test tone
        n       = int(self.sample_rate * duration)
        t_arr   = np.linspace(0, duration, n, endpoint=False)
        tone    = (self.amplitude * np.sin(2 * np.pi * test_freq * t_arr)).astype(np.float32)
        fft_mag = np.abs(np.fft.rfft(tone, n=FFT_SIZE))
        freqs   = np.fft.rfftfreq(FFT_SIZE, 1.0 / self.sample_rate)
        peak    = freqs[np.argmax(fft_mag)]

        return {
            "ok":            abs(peak - test_freq) < self.freq_step / 2,
            "test_freq":     test_freq,
            "detected_freq": float(peak),
            "error_hz":      float(abs(peak - test_freq)),
            "sample_rate":   self.sample_rate,
            "symbol_ms":     self.symbol_ms,
            "fsk_band":      f"{self.freq_base}–{self.freq_base + 15 * self.freq_step} Hz",
        }

    # ── Simulated transmit/receive (no audio hardware) ────────
    def simulate_transmit_receive(self, data: bytes) -> bytes:
        """
        Loopback test — encode then decode without audio hardware.
        Useful for unit testing and development.
        """
        decoded = []
        for byte_val in data:
            high_nibble = (byte_val >> 4) & 0x0F
            low_nibble  = byte_val & 0x0F

            high_tone = self._nibble_to_tone(high_nibble)
            low_tone  = self._nibble_to_tone(low_nibble)

            detected_high = self._detect_nibble(high_tone)
            detected_low  = self._detect_nibble(low_tone)

            decoded.append((detected_high << 4) | detected_low)

        return bytes(decoded)
