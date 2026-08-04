from __future__ import annotations
import time
from typing import Optional, Tuple
from lab_device_manager.instruments.base import StatusSnapshot, offline_snapshot

# Fangrui rotary viscometer (上海方瑞, e.g. LVDV-2T) — custom binary UPLOAD protocol.
# The device PUSHES data frames to the host (9600 8N1, confirmed on hardware).
# It is not Modbus.
#
# Frame layout (29 bytes), confirmed against live LVDV-2T captures and
# 粘度计/粘度计上位机通信协议（19）.pdf:
#   [0:4]   header  D6 C8 18 32
#   [4]     space   0x20
#   [5:9]   viscosity * 100   (4B big-endian, mPa·s)
#   [9]     space
#   [10:12] temperature * 10  (2B big-endian, deg C)
#   [12]    space
#   [13:17] shear rate * 1000 (4B big-endian, 1/s; live-confirmed)
#   [17]    space
#   [18:22] shear stress * 10 (4B big-endian, mPa on the instrument screen)
#   [22]    space
#   [23:25] torque * 10       (2B big-endian, %; live-confirmed)
#   [25]    space
#   [26]    checksum          sum(bytes[0:26]) modulo 256
#   [27:29] trailer           13 AB
#
# A second frame type D6 C8 16 35 ... exists; meaning undocumented. Not parsed here.
# A possible second frame type is still undocumented and was not seen in capture.

HEADER = b"\xd6\xc8\x18\x32"
TRAILER = b"\x13\xab"
FRAME_LEN = 29
MAX_BUFFER_BYTES = FRAME_LEN * 2
SEPARATOR_OFFSETS = (4, 9, 12, 17, 22, 25)


def checksum_sum256(frame: bytes) -> int:
    """Return the LVDV-2T checksum: bytes 0..25 summed modulo 256."""
    return sum(frame[0:26]) & 0xFF


def is_valid_viscometer_frame(frame: bytes) -> bool:
    """Validate the documented fixed layout and live-confirmed checksum."""
    return (
        len(frame) == FRAME_LEN
        and frame.startswith(HEADER)
        and frame.endswith(TRAILER)
        and all(frame[offset] == 0x20 for offset in SEPARATOR_OFFSETS)
        and frame[26] == checksum_sum256(frame)
    )


def parse_viscometer_frame(frame: bytes) -> dict:
    """Decode a validated 29-byte frame into documented physical values."""
    if not is_valid_viscometer_frame(frame):
        raise ValueError("invalid Fangrui viscometer frame")
    viscosity = int.from_bytes(frame[5:9], "big") / 100.0
    temperature = int.from_bytes(frame[10:12], "big") / 10.0
    shear_rate = int.from_bytes(frame[13:17], "big") / 1000.0
    shear_stress_mpa = int.from_bytes(frame[18:22], "big") / 10.0
    torque_pct = int.from_bytes(frame[23:25], "big") / 10.0
    return {
        "viscosity_mPas": viscosity,
        "temperature_c": temperature,
        "shear_rate_1s": shear_rate,
        "shear_stress_mPa": shear_stress_mpa,
        "shear_stress_Pa": round(shear_stress_mpa / 1000.0, 7),
        "torque_pct": torque_pct,
    }


def extract_latest_frame(buf: bytes) -> Tuple[Optional[bytes], bytes]:
    """Find the LAST complete header..trailer frame in buf.
    Returns (frame_bytes or None, remaining_bytes_after_that_frame).
    'Complete' = HEADER present, FRAME_LEN bytes available and fixed layout valid.
    The checksum is validated before a frame is returned."""
    last_idx = -1
    search_from = 0
    while True:
        idx = buf.find(HEADER, search_from)
        if idx == -1:
            break
        end = idx + FRAME_LEN
        if end <= len(buf) and is_valid_viscometer_frame(buf[idx:end]):
            last_idx = idx          # remember the latest valid frame start
        search_from = idx + 1
    if last_idx == -1:
        return None, buf
    end = last_idx + FRAME_LEN
    return buf[last_idx:end], buf[end:]


class ViscometerAdapter:
    """Read the Fangrui device's pushed stream and return its latest frame."""

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
            return offline_snapshot(
                "Fangrui Viscometer",
                "no valid viscometer data frame",
                communication_status="instrument_unresponsive",
            )
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
                "shear_stress_mPa": d["shear_stress_mPa"],
                "shear_stress_Pa": d["shear_stress_Pa"],
                "torque_pct": d["torque_pct"],
                "checksum_rx": frame[26],
                "checksum_sum256": checksum_sum256(frame),
                "data_verified": True,
                "checksum_validated": True,
                "parser_version": "live-screen-v3",
            },
        )
