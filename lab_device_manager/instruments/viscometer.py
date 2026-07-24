from __future__ import annotations
import time
from typing import Optional, Tuple
from lab_device_manager.instruments.base import StatusSnapshot

# Fangrui rotary viscometer (上海方瑞, e.g. LVDV-1T) — custom binary UPLOAD protocol.
# The device PUSHES data frames to the host (9600 8N1, assumed). NOT Modbus.
#
# Frame layout (28 bytes), per 粘度计/协议规范.docx:
#   [0:4]   header  D6 C8 18 32
#   [4]     space   0x20
#   [5:9]   viscosity * 100   (4B big-endian, mPa·s)
#   [9]     space
#   [10:12] temperature * 10  (2B big-endian, deg C)
#   [12]    space
#   [13:17] shear rate * 100  (4B big-endian, 1/s)
#   [17]    space
#   [18:22] shear stress * 10 (4B big-endian, Pa)
#   [22]    space
#   [23:25] torque * 10000    (2B big-endian, %)  -- scaling is a GUESS, see parse
#   [25]    space
#   [26]    checksum          (algorithm NOT documented)
#   [27]    trailer           0x40
#
# A second frame type D6 C8 16 35 ... exists; meaning undocumented. Not parsed here.
# UNVERIFIED (need live capture): checksum algorithm, torque scaling, 2nd frame type,
# push trigger, physical layer (RS232 vs USB).

HEADER = b"\xd6\xc8\x18\x32"
TRAILER = 0x40
FRAME_LEN = 28
MAX_BUFFER_BYTES = FRAME_LEN * 2


def parse_viscometer_frame(frame: bytes) -> dict:
    """Decode a 28-byte frame into physical values per the documented layout."""
    viscosity = int.from_bytes(frame[5:9], "big") / 100.0
    temperature = int.from_bytes(frame[10:12], "big") / 10.0
    shear_rate = int.from_bytes(frame[13:17], "big") / 100.0
    shear_stress = int.from_bytes(frame[18:22], "big") / 10.0
    torque_raw = int.from_bytes(frame[23:25], "big")
    # GUESS: spec says torque*10000 in 2 bytes. Treating raw as fraction*10000,
    # so percent = raw/100 (e.g. raw 5000 -> 50%). Confirm against live data.
    torque_pct = torque_raw / 100.0
    return {
        "viscosity_mPas": viscosity,
        "temperature_c": temperature,
        "shear_rate_1s": shear_rate,
        "shear_stress_Pa": shear_stress,
        "torque_pct": torque_pct,
    }


def guess_checksum_sum256(frame: bytes) -> int:
    """Best-guess checksum (sum of bytes[0:26] mod 256). UNVERIFIED — diagnostics only,
    never used to reject frames until the real algorithm is confirmed from live data."""
    return sum(frame[0:26]) % 256


def extract_latest_frame(buf: bytes) -> Tuple[Optional[bytes], bytes]:
    """Find the LAST complete header..trailer frame in buf.
    Returns (frame_bytes or None, remaining_bytes_after_that_frame).
    'Complete' = HEADER present, FRAME_LEN bytes available, TRAILER at last byte.
    Checksum is NOT validated (algorithm unknown)."""
    last_idx = -1
    search_from = 0
    while True:
        idx = buf.find(HEADER, search_from)
        if idx == -1:
            break
        end = idx + FRAME_LEN
        if end <= len(buf) and buf[end - 1] == TRAILER:
            last_idx = idx          # remember the latest valid frame start
        search_from = idx + 1
    if last_idx == -1:
        return None, buf
    end = last_idx + FRAME_LEN
    return buf[last_idx:end], buf[end:]


class ViscometerAdapter:
    """Fangrui viscometer. Reads the device's pushed binary stream and returns the
    latest complete frame as a StatusSnapshot. PROVISIONAL — pending live capture
    to confirm checksum / torque scaling / frame types / push trigger."""

    def __init__(self, transport, read_timeout: float = 0.5):
        self.transport = transport
        self.read_timeout = read_timeout
        self._buffer = bytearray()

    def identity(self) -> str:
        return "Fangrui Viscometer"

    def read_status(self) -> StatusSnapshot:
        # Drain whatever bytes are currently buffered (short polls until a gap),
        # then return the LATEST complete frame so stale queued data is skipped.
        deadline = time.monotonic() + self.read_timeout
        while time.monotonic() < deadline:
            chunk = self.transport.read_wait(0.02)
            if not chunk:
                break
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_BUFFER_BYTES:
                del self._buffer[:-MAX_BUFFER_BYTES]
        frame, remaining = extract_latest_frame(bytes(self._buffer))
        if frame is None:
            return StatusSnapshot(timestamp=time.time(), state="offline",
                                  work_mode="", device_id="Fangrui Viscometer")
        self._buffer = bytearray(remaining[-MAX_BUFFER_BYTES:])
        return self._to_snapshot(frame)

    def _to_snapshot(self, frame: bytes) -> StatusSnapshot:
        d = parse_viscometer_frame(frame)
        return StatusSnapshot(
            timestamp=time.time(),
            state="running",
            work_mode="viscosity",
            device_id="Fangrui Viscometer",
            temp_c=d["temperature_c"],
            alarm=False,
            metrics={
                "viscosity_mPas": d["viscosity_mPas"],
                "shear_rate_1s": d["shear_rate_1s"],
                "shear_stress_Pa": d["shear_stress_Pa"],
                "torque_pct": d["torque_pct"],
                "checksum_rx": frame[26],
                "checksum_guess_sum256": guess_checksum_sum256(frame),
            },
        )
