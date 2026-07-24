from __future__ import annotations

import time
import serial
from typing import Protocol

FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04


def crc16(data: bytes) -> int:
    """Modbus RTU CRC-16 (poly 0xA001)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def append_crc(frame: bytes) -> bytes:
    c = crc16(frame)
    return frame + bytes([c & 0xFF, (c >> 8) & 0xFF])


def build_read_request(slave: int, fc: int, reg: int, count: int) -> bytes:
    body = bytes([slave & 0xFF, fc & 0xFF,
                  (reg >> 8) & 0xFF, reg & 0xFF,
                  (count >> 8) & 0xFF, count & 0xFF])
    return append_crc(body)


class ModbusError(Exception):
    def __init__(self, slave: int, fc: int, code: int):
        self.slave, self.fc, self.code = slave, fc, code
        super().__init__(f"Modbus exception slave={slave} fc=0x{fc:02x} code=0x{code:02x}")


def parse_response(
    frame: bytes,
    slave: int,
    fc: int,
    expected_data_bytes: int | None = None,
) -> bytes:
    """Validate CRC/address/fc; return payload data bytes."""
    if len(frame) < 5:
        raise ValueError(f"short frame ({len(frame)}b): {frame.hex()}")
    payload, recv = frame[:-2], frame[-2:]
    c = crc16(payload)
    if recv != bytes([c & 0xFF, (c >> 8) & 0xFF]):
        raise ValueError(f"CRC mismatch: {frame.hex()}")
    if payload[0] != slave:
        raise ValueError(f"slave mismatch got={payload[0]} want={slave}")
    got_fc = payload[1]
    if got_fc & 0x80:
        code = payload[2] if len(payload) > 2 else 0
        raise ModbusError(slave, got_fc & 0x7F, code)
    if got_fc != fc:
        raise ValueError(f"fc mismatch got=0x{got_fc:02x} want=0x{fc:02x}")
    byte_count = payload[2]
    data = payload[3:]
    if len(data) != byte_count:
        raise ValueError(
            f"byte count mismatch got={len(data)} declared={byte_count}"
        )
    if expected_data_bytes is not None and byte_count != expected_data_bytes:
        raise ValueError(
            f"response length mismatch got={byte_count} expected={expected_data_bytes}"
        )
    return data


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...
    def read_wait(self, timeout_s: float) -> bytes: ...
    def flush(self) -> None: ...


class ModbusClient:
    """Sends a Modbus RTU request and reads one response frame."""

    def __init__(self, transport: "Transport", slave: int = 1,
                 read_timeout: float = 0.4, gap_timeout: float = 0.06,
                 pre_silence_s: float = 0.004):
        self.transport = transport
        self.slave = slave
        self.read_timeout = read_timeout
        self.gap_timeout = gap_timeout
        self.pre_silence_s = pre_silence_s

    def transact(self, request: bytes) -> bytes:
        self.transport.flush()
        if self.pre_silence_s:
            time.sleep(self.pre_silence_s)
        self.transport.write(request)
        buf = bytearray()
        deadline = time.monotonic() + self.read_timeout
        while time.monotonic() < deadline:
            chunk = self.transport.read_wait(self.gap_timeout)
            if chunk:
                buf.extend(chunk)
                continue
            if buf:
                break
        if not buf:
            raise TimeoutError("no Modbus response within read_timeout")
        register_count = int.from_bytes(request[4:6], "big")
        return parse_response(
            bytes(buf),
            self.slave,
            request[1],
            expected_data_bytes=register_count * 2,
        )

    def read_input_registers(self, reg: int, count: int) -> bytes:
        return self.transact(build_read_request(self.slave, FC_READ_INPUT, reg, count))

    def read_holding_registers(self, reg: int, count: int) -> bytes:
        return self.transact(build_read_request(self.slave, FC_READ_HOLDING, reg, count))


def _parity_code(parity: str) -> str:
    p = parity.upper()
    if p == "NONE":
        return "N"
    if p == "EVEN":
        return "E"
    if p == "ODD":
        return "O"
    raise ValueError(f"unsupported parity {parity}")


class SerialTransport:
    """RS485/FTDI serial transport, 8 data bits + configurable parity + 1 stop."""

    def __init__(self, port: str, baudrate: int = 9600, parity: str = "EVEN"):
        self.port = port
        self.baudrate = baudrate
        self.parity = parity
        self._ser = None

    def open(self) -> None:
        opened = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            parity=_parity_code(self.parity),
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            timeout=0,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self._ser = opened
        try:
            opened.reset_input_buffer()
            opened.reset_output_buffer()
        except Exception:
            opened.close()
            self._ser = None
            raise

    def write(self, data: bytes) -> None:
        assert self._ser is not None
        self._ser.write(data)
        self._ser.flush()

    def read_wait(self, timeout_s: float) -> bytes:
        assert self._ser is not None
        self._ser.timeout = timeout_s
        return self._ser.read(256)

    def flush(self) -> None:
        if self._ser is not None:
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()

    def drain(self) -> None:
        if self._ser is not None:
            self._ser.flush()

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None
