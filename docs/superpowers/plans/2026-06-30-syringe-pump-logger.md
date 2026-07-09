# TYD02 注射泵 实时监控与记录台 (Phase-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only local web app that polls the Leadfluid TYD02 syringe pump over RS485/Modbus, shows a live dashboard, and writes per-session CSV logs (research data archival).

**Architecture:** Background sampler thread → immutable `StatusSnapshot` → two subscribers: a CSV `SessionLogger` and a Flask web server that pushes snapshots to the browser via SSE. An `InstrumentAdapter` interface isolates pump-specific logic so stirrer/viscometer adapters slot in later. Serial I/O is split into pure framing (unit-tested with real captured frames) + a thin `SerialTransport` (termios 8E1, integration-tested on hardware).

**Tech Stack:** Python 3.14 (stdlib `termios`, `tomllib`, `struct`, `csv`, `threading`), Flask (only external dep), pytest. Offline Chart.js vendored for the optional chart.

## Global Constraints

- **Serial: `9600, 8E1` (even parity), slave address `1`, Modbus RTU, CRC-16 low-byte-first.** Verbatim from spec §3.
- **Phase-1 is strictly READ-ONLY.** No write function codes (0x05/0x06/0x0F/0x10) anywhere. "Start/Stop session" controls logging only, never the pump.
- Runs on macOS, single-user `localhost`, offline (no CDN).
- One external runtime dependency: `flask`. Config via `config.toml` (stdlib `tomllib`).
- Commits per task; commit messages `<type>: <desc>` (attribution disabled per user global config — do NOT add Co-Authored-By).
- Tests via `pytest` from project root; target ≥80% coverage.
- Source package `pump_monitor/` (ASCII name — avoids import issues with the Chinese project path `设备测试`).

## File Structure

```
设备测试/
├── pump_monitor/
│   ├── __init__.py
│   ├── __main__.py            # entry: wire everything, open browser, run Flask
│   ├── config.py              # Config dataclass + load_config (tomllib)
│   ├── modbus_io.py           # framing + ModbusClient + SerialTransport (8E1)
│   ├── sampler.py             # background polling thread
│   ├── session_logger.py      # CSV session writer (research data archival)
│   ├── instruments/
│   │   ├── __init__.py
│   │   ├── base.py            # StatusSnapshot + InstrumentAdapter + offline_snapshot
│   │   └── leadfluid_tyd02.py # TYD02Adapter: register map, decode, state derivation
│   └── web/
│       ├── __init__.py
│       ├── app.py             # Flask app factory + SSE
│       └── static/
│           ├── index.html
│           ├── app.js
│           └── chart.min.js   # vendored (Task 11, optional chart)
├── tests/
│   ├── test_config.py
│   ├── modbus/test_frame.py
│   ├── modbus/test_client.py
│   ├── modbus/test_serial_helpers.py
│   ├── instruments/test_base.py
│   ├── instruments/test_tyd02_decode.py
│   ├── instruments/test_tyd02_adapter.py
│   ├── test_sampler.py
│   ├── test_session_logger.py
│   └── web/test_app.py
├── scripts/
│   └── smoke_live.py          # manual hardware smoke test
├── config.toml
├── requirements.txt           # flask
├── requirements-dev.txt       # pytest
└── pyproject.toml             # pytest config
```

Each task produces an independently testable deliverable. Tests use captured real Modbus frames (request `01 04 03 fa 00 05 10 7c`; response containing `LeadFluid`) so the framing layer is pinned to ground truth.

---

### Task 1: Project scaffolding + config loader

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `config.toml`
- Create: `pump_monitor/__init__.py` (empty), `pump_monitor/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `pump_monitor.config.Config` (frozen dataclass), `load_config(path="config.toml") -> Config`. Fields used by later tasks: `serial_port, baudrate, parity, slave_address, sample_interval_ms, web_port, log_dir, auto_open_browser, wordorder`.

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
import os, textwrap
from pump_monitor.config import load_config

def test_load_config_reads_all_fields(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent("""
        serial_port = "/dev/cu.usbserial-XX"
        baudrate = 9600
        parity = "EVEN"
        slave_address = 1
        sample_interval_ms = 1000
        web_port = 7800
        log_dir = "./data/sessions"
        auto_open_browser = true
        wordorder = "CDAB"
    """), encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.serial_port == "/dev/cu.usbserial-XX"
    assert cfg.baudrate == 9600
    assert cfg.parity == "EVEN"
    assert cfg.slave_address == 1
    assert cfg.sample_interval_ms == 1000
    assert cfg.web_port == 7800
    assert cfg.log_dir == "./data/sessions"
    assert cfg.auto_open_browser is True
    assert cfg.wordorder == "CDAB"

def test_load_config_applies_defaults(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('serial_port = "/dev/x"\n', encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.baudrate == 9600 and cfg.parity == "EVEN"
    assert cfg.slave_address == 1 and cfg.wordorder == "CDAB"
    assert cfg.web_port == 7800 and cfg.auto_open_browser is True

def test_config_is_frozen(tmp_path):
    import dataclasses
    p = tmp_path / "c.toml"
    p.write_text('serial_port = "/dev/x"\n', encoding="utf-8")
    cfg = load_config(str(p))
    try:
        cfg.baudrate = 19200
        assert False, "should be frozen"
    except dataclasses.FrozenInstanceError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pump_monitor'`

- [ ] **Step 3: Write minimal implementation**

`requirements.txt`:
```
flask>=3.0
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.0
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"
```

`config.toml`:
```toml
serial_port        = "/dev/cu.usbserial-BG039GXP"
baudrate           = 9600
parity             = "EVEN"        # 8E1
slave_address      = 1
sample_interval_ms = 1000
web_port           = 7800
log_dir            = "./data/sessions"
auto_open_browser  = true
wordorder          = "CDAB"        # pump float byte-order; auto-detected at runtime
```

`pump_monitor/__init__.py`: (empty file)

`pump_monitor/config.py`:
```python
from __future__ import annotations
import tomllib
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    serial_port: str
    baudrate: int = 9600
    parity: str = "EVEN"
    slave_address: int = 1
    sample_interval_ms: int = 1000
    web_port: int = 7800
    log_dir: str = "./data/sessions"
    auto_open_browser: bool = True
    wordorder: str = "CDAB"


def load_config(path: str = "config.toml") -> Config:
    with open(path, "rb") as f:
        d = tomllib.load(f)
    return Config(
        serial_port=d["serial_port"],
        baudrate=d.get("baudrate", 9600),
        parity=d.get("parity", "EVEN"),
        slave_address=d.get("slave_address", 1),
        sample_interval_ms=d.get("sample_interval_ms", 1000),
        web_port=d.get("web_port", 7800),
        log_dir=d.get("log_dir", "./data/sessions"),
        auto_open_browser=d.get("auto_open_browser", True),
        wordorder=d.get("wordorder", "CDAB"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install -r requirements-dev.txt && pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt requirements-dev.txt pyproject.toml config.toml pump_monitor/__init__.py pump_monitor/config.py tests/test_config.py
git commit -m "feat: project scaffold + config loader"
```

---

### Task 2: Modbus RTU framing (pure functions)

**Files:**
- Create: `pump_monitor/modbus_io.py`
- Test: `tests/modbus/__init__.py` (empty), `tests/modbus/test_frame.py`

**Interfaces:**
- Produces: `FC_READ_HOLDING=0x03`, `FC_READ_INPUT=0x04`, `crc16(data)->int`, `append_crc(frame)->bytes`, `build_read_request(slave,fc,reg,count)->bytes`, `parse_response(frame,slave,fc)->bytes` (returns data bytes; raises `ModbusError`/`ValueError`).

- [ ] **Step 1: Write the failing test**

`tests/modbus/test_frame.py`:
```python
import pytest
from pump_monitor.modbus_io import (
    crc16, append_crc, build_read_request, parse_response, ModbusError,
    FC_READ_INPUT,
)

# Real captured frame: read company-info @1018, count 5, slave 1.
def test_crc16_known_vector():
    assert crc16(bytes.fromhex("010403fa0005")) == 0x7C10

def test_append_crc_low_byte_first():
    assert append_crc(bytes.fromhex("010403fa0005")) == bytes.fromhex("010403fa0005107c")

def test_build_read_request_matches_capture():
    req = build_read_request(1, FC_READ_INPUT, 1018, 5)
    assert req == bytes.fromhex("010403fa0005107c")

def test_parse_response_returns_data_bytes():
    # captured reply: slave=1 fc=04 bytecount=0a + 10 data bytes + CRC 5c03
    frame = bytes.fromhex("01040a654c64616c46697500645c03")
    assert parse_response(frame, 1, FC_READ_INPUT) == bytes.fromhex("654c64616c4669750064")

def test_parse_response_raises_on_bad_crc():
    bad = bytes([1, 4, 2, 0, 0, 0xAA, 0xBB])
    with pytest.raises(ValueError):
        parse_response(bad, 1, FC_READ_INPUT)

def test_parse_response_raises_modbus_exception():
    exc = append_crc(bytes([1, 0x84, 0x02]))  # exception: fc|0x80, code=2
    with pytest.raises(ModbusError) as ei:
        parse_response(exc, 1, FC_READ_INPUT)
    assert ei.value.code == 0x02
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/modbus/test_frame.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pump_monitor.modbus_io'`

- [ ] **Step 3: Write minimal implementation**

`pump_monitor/modbus_io.py`:
```python
from __future__ import annotations

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
    return frame + bytes([c & 0xFF, (c >> 8) & 0xFF])  # low byte first


def build_read_request(slave: int, fc: int, reg: int, count: int) -> bytes:
    body = bytes([slave & 0xFF, fc & 0xFF,
                  (reg >> 8) & 0xFF, reg & 0xFF,
                  (count >> 8) & 0xFF, count & 0xFF])
    return append_crc(body)


class ModbusError(Exception):
    def __init__(self, slave: int, fc: int, code: int):
        self.slave, self.fc, self.code = slave, fc, code
        super().__init__(f"Modbus exception slave={slave} fc=0x{fc:02x} code=0x{code:02x}")


def parse_response(frame: bytes, slave: int, fc: int) -> bytes:
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
    return payload[3:]  # skip byte-count byte
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/modbus/test_frame.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/modbus_io.py tests/modbus/__init__.py tests/modbus/test_frame.py
git commit -m "feat: modbus rtu framing with crc + exception parsing"
```

### Task 3: ModbusClient + Transport interface

Separates the request/response loop (unit-testable with a fake transport) from real serial I/O.

**Files:**
- Modify: `pump_monitor/modbus_io.py` (append)
- Test: `tests/modbus/test_client.py`

**Interfaces:**
- Consumes: `build_read_request`, `parse_response`, `FC_READ_INPUT/HOLDING` (Task 2).
- Produces: `Transport` Protocol (`write/read_wait/flush`), `ModbusClient(transport, slave, read_timeout, gap_timeout, pre_silence_s)` with `transact(request)->bytes`, `read_input_registers(reg,count)->bytes`, `read_holding_registers(reg,count)->bytes`. Raises `TimeoutError` when no reply.

- [ ] **Step 1: Write the failing test**

`tests/modbus/test_client.py`:
```python
import pytest
from pump_monitor.modbus_io import ModbusClient


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.written = []
    def write(self, data):
        self.written.append(data)
    def read_wait(self, timeout_s):
        if self.responses:
            return self.responses.pop(0)
        return b""
    def flush(self):
        pass


def _client(responses):
    return ModbusClient(FakeTransport(responses), slave=1,
                        gap_timeout=0.0, read_timeout=0.5, pre_silence_s=0.0)


def test_client_returns_payload_and_writes_request():
    resp = bytes.fromhex("01040a654c64616c46697500645c03")
    c = _client([resp])
    data = c.read_input_registers(1018, 5)
    assert data == bytes.fromhex("654c64616c4669750064")
    assert c.transport.written[0] == bytes.fromhex("010403fa0005107c")


def test_client_timeout_when_no_response():
    c = _client([])
    c.read_timeout = 0.05
    with pytest.raises(TimeoutError):
        c.read_input_registers(1018, 5)


def test_client_propagates_crc_error():
    c = _client([bytes([1, 4, 2, 0, 0, 0xAA, 0xBB])])
    with pytest.raises(ValueError):
        c.read_input_registers(1018, 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/modbus/test_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'ModbusClient'`

- [ ] **Step 3: Write minimal implementation**

Add to top of `pump_monitor/modbus_io.py`:
```python
import time
from typing import Protocol
```

Append to `pump_monitor/modbus_io.py`:
```python
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
            time.sleep(self.pre_silence_s)  # RTU >=3.5 char silence before frame
        self.transport.write(request)
        buf = bytearray()
        deadline = time.monotonic() + self.read_timeout
        while time.monotonic() < deadline:
            chunk = self.transport.read_wait(self.gap_timeout)
            if chunk:
                buf.extend(chunk)
                continue
            if buf:  # inter-byte gap after data => frame complete
                break
        if not buf:
            raise TimeoutError("no Modbus response within read_timeout")
        return parse_response(bytes(buf), self.slave, request[1])

    def read_input_registers(self, reg: int, count: int) -> bytes:
        return self.transact(build_read_request(self.slave, FC_READ_INPUT, reg, count))

    def read_holding_registers(self, reg: int, count: int) -> bytes:
        return self.transact(build_read_request(self.slave, FC_READ_HOLDING, reg, count))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/modbus/test_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/modbus_io.py tests/modbus/test_client.py
git commit -m "feat: modbus client with injectable transport"
```

---

### Task 4: SerialTransport (termios 8E1) + hardware smoke

The pure baud/parity helpers are unit-tested; the termios syscall wrapper is integration-verified by the smoke script against the real pump (termios cannot be exercised without hardware).

**Files:**
- Modify: `pump_monitor/modbus_io.py` (append imports + helpers + `SerialTransport`)
- Create: `scripts/smoke_live.py`
- Test: `tests/modbus/test_serial_helpers.py`

**Interfaces:**
- Produces: `baud_constant(baud)->int`, `parity_flags(parity)->(cflag_bits, iflag_bits)`, `SerialTransport(port, baudrate, parity)` implementing `Transport` (`open/close/write/read_wait/flush/drain`). Consumed by Task 8 wiring.

- [ ] **Step 1: Write the failing test**

`tests/modbus/test_serial_helpers.py`:
```python
import termios
import pytest
from pump_monitor.modbus_io import baud_constant, parity_flags


def test_baud_constant_known():
    assert baud_constant(9600) == termios.B9600
    assert baud_constant(4800) == termios.B4800
    assert baud_constant(38400) == termios.B38400


def test_baud_constant_rejects_unknown():
    with pytest.raises(ValueError):
        baud_constant(12345)


def test_parity_flags_even():
    c, i = parity_flags("EVEN")
    assert c & termios.PARENB
    assert not (c & termios.PARODD)
    assert i & termios.IGNPAR


def test_parity_flags_odd():
    c, _ = parity_flags("odd")
    assert c & termios.PARENB and c & termios.PARODD


def test_parity_flags_none():
    c, i = parity_flags("none")
    assert c == 0 and i == 0


def test_parity_flags_invalid():
    with pytest.raises(ValueError):
        parity_flags("MARK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/modbus/test_serial_helpers.py -v`
Expected: FAIL — `ImportError: cannot import name 'baud_constant'`

- [ ] **Step 3: Write minimal implementation**

Add to the top imports of `pump_monitor/modbus_io.py`:
```python
import os
import select
import termios
import fcntl
```

Append to `pump_monitor/modbus_io.py`:
```python
_BAUD = {4800: termios.B4800, 9600: termios.B9600,
         19200: termios.B19200, 38400: termios.B38400}


def baud_constant(baud: int) -> int:
    if baud not in _BAUD:
        raise ValueError(f"unsupported baud {baud}")
    return _BAUD[baud]


def parity_flags(parity: str):
    """Return (cflag_bits, iflag_bits) for the given parity setting."""
    p = parity.upper()
    if p == "NONE":
        return 0, 0
    if p == "EVEN":
        return termios.PARENB, termios.IGNPAR
    if p == "ODD":
        return termios.PARENB | termios.PARODD, termios.IGNPAR
    raise ValueError(f"unsupported parity {parity}")


class SerialTransport:
    """RS485/FTDI serial transport, 8 data bits + configurable parity + 1 stop."""

    def __init__(self, port: str, baudrate: int = 9600, parity: str = "EVEN"):
        self.port = port
        self.baudrate = baudrate
        self.parity = parity
        self._fd = None

    def open(self) -> None:
        fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(fd)
        pcflag, piflag = parity_flags(self.parity)
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL | pcflag
        cflag &= ~termios.CSTOPB
        cflag &= ~termios.CSIZE
        cflag |= termios.CS8
        try:
            cflag &= ~termios.CRTSCTS
        except AttributeError:
            pass
        iflag = piflag
        iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY)
        oflag = 0
        lflag = 0
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        speed = baud_constant(self.baudrate)
        termios.tcsetattr(fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, speed, speed, cc])
        termios.tcflush(fd, termios.TCIOFLUSH)
        self._fd = fd

    def write(self, data: bytes) -> None:
        assert self._fd is not None
        os.write(self._fd, data)

    def read_wait(self, timeout_s: float) -> bytes:
        assert self._fd is not None
        r, _, _ = select.select([self._fd], [], [], timeout_s)
        if not r:
            return b""
        try:
            return os.read(self._fd, 256)
        except BlockingIOError:
            return b""

    def flush(self) -> None:
        if self._fd is not None:
            termios.tcflush(self._fd, termios.TCIOFLUSH)

    def drain(self) -> None:
        if self._fd is not None:
            try:
                termios.tcdrain(self._fd)
            except termios.error:
                pass

    def close(self) -> None:
        if self._fd is not None:
            termios.tcflush(self._fd, termios.TCIOFLUSH)
            os.close(self._fd)
            self._fd = None
```

`scripts/smoke_live.py`:
```python
#!/usr/bin/env python3
"""Manual hardware smoke test for serial transport + framing.
Run with the pump powered & connected:  python scripts/smoke_live.py
Expected: prints raw company-info bytes '65 4c 64 61 6c 46 69 75 00 64'."""
from pump_monitor.config import load_config
from pump_monitor.modbus_io import SerialTransport, ModbusClient


def main():
    cfg = load_config()
    tr = SerialTransport(cfg.serial_port, cfg.baudrate, cfg.parity)
    tr.open()
    try:
        c = ModbusClient(tr, slave=cfg.slave_address)
        raw = c.read_input_registers(1018, 5)  # company info @1018, 5 regs
        print("company raw:", raw.hex(" "))
    finally:
        tr.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes; run smoke on hardware**

Run: `pytest tests/modbus/test_serial_helpers.py -v`
Expected: PASS (6 tests)

Manual (pump connected): `python scripts/smoke_live.py`
Expected: `company raw: 65 4c 64 61 6c 46 69 75 00 64`

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/modbus_io.py scripts/smoke_live.py tests/modbus/test_serial_helpers.py
git commit -m "feat: serial transport (8E1) + live smoke test"
```

### Task 5: Instrument base types

**Files:**
- Create: `pump_monitor/instruments/__init__.py` (empty), `pump_monitor/instruments/base.py`
- Test: `tests/instruments/__init__.py` (empty), `tests/instruments/test_base.py`

**Interfaces:**
- Produces: frozen `StatusSnapshot` dataclass, `InstrumentAdapter` Protocol (`identity()->str`, `read_status()->StatusSnapshot`), `offline_snapshot(device_id, reason)->StatusSnapshot`. Consumed by Tasks 7 & 8.

- [ ] **Step 1: Write the failing test**

`tests/instruments/test_base.py`:
```python
import dataclasses
import time
from pump_monitor.instruments.base import StatusSnapshot, offline_snapshot


def _snap(**kw):
    base = dict(timestamp=time.time(), state="running",
                work_mode="仅注入", device_id="LeadFluid TYD02")
    base.update(kw)
    return StatusSnapshot(**base)


def test_snapshot_is_frozen():
    s = _snap()
    try:
        s.state = "stopped"
        assert False, "should be frozen"
    except dataclasses.FrozenInstanceError:
        pass


def test_snapshot_defaults():
    s = _snap()
    assert s.flow_rpm is None
    assert s.alarm is False
    assert s.acc_unit == ""
    assert s.progress_pct is None


def test_offline_snapshot():
    s = offline_snapshot("dev1", "timeout")
    assert s.state == "offline"
    assert s.device_id == "dev1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/instruments/test_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pump_monitor.instruments'`

- [ ] **Step 3: Write minimal implementation**

`pump_monitor/instruments/base.py`:
```python
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class StatusSnapshot:
    timestamp: float
    state: str                       # running | paused | stopped | alarm | offline
    work_mode: str
    device_id: str
    flow_rpm: Optional[float] = None
    acc_volume: Optional[float] = None
    acc_unit: str = ""
    consumed_volume: Optional[float] = None
    consumed_unit: str = ""
    remaining_volume: Optional[float] = None
    remaining_unit: str = ""
    elapsed_ms: Optional[int] = None
    remaining_ms: Optional[int] = None
    cycles: Optional[int] = None
    progress_pct: Optional[float] = None
    temp_c: Optional[float] = None
    alarm: bool = False
    error_code: Optional[int] = None


class InstrumentAdapter(Protocol):
    def identity(self) -> str: ...
    def read_status(self) -> StatusSnapshot: ...


def offline_snapshot(device_id: str, reason: str = "") -> StatusSnapshot:
    return StatusSnapshot(timestamp=time.time(), state="offline",
                          work_mode="", device_id=device_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/instruments/test_base.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/instruments/__init__.py pump_monitor/instruments/base.py tests/instruments/__init__.py tests/instruments/test_base.py
git commit -m "feat: status snapshot + instrument adapter interface"
```

---

### Task 6: Leadfluid decode helpers

Pure decoders for the pump's quirks: ASCII stored as 16-bit little-endian words; floats/uint32 with ABCD or CDAB word order.

**Files:**
- Create: `pump_monitor/instruments/leadfluid_tyd02.py`
- Test: `tests/instruments/test_tyd02_decode.py`

**Interfaces:**
- Produces: `VOLUME_UNITS`, `WORK_MODES` dicts; `decode_ascii(raw)->str`, `decode_float(raw,wordorder)->float`, `decode_uint32(raw,wordorder)->int`, `decode_uint16(raw)->int`, `decode_int16(raw)->int`.

- [ ] **Step 1: Write the failing test**

`tests/instruments/test_tyd02_decode.py`:
```python
import struct
import pytest
from pump_monitor.instruments.leadfluid_tyd02 import (
    decode_ascii, decode_float, decode_uint32, decode_uint16, decode_int16,
    VOLUME_UNITS, WORK_MODES,
)


def test_decode_ascii_leadfluid_capture():
    # real captured company-info data bytes
    assert decode_ascii(bytes.fromhex("654c64616c4669750064")) == "LeadFluid"


def test_decode_ascii_odd_length():
    # 'A' alone -> stored as (00, 0x41); 2 bytes
    assert decode_ascii(bytes([0x00, 0x41])) == "A"


def test_decode_float_cdab_roundtrip():
    v = 12.5
    b = struct.pack(">f", v)
    cdab = bytes([b[2], b[3], b[0], b[1]])
    assert decode_float(cdab, "CDAB") == pytest.approx(v)


def test_decode_float_abcd_roundtrip():
    v = 0.001
    assert decode_float(struct.pack(">f", v), "ABCD") == pytest.approx(v)


def test_decode_uint32_both_orders():
    abcd = bytes([0x01, 0x02, 0x03, 0x04])
    cdab = bytes([0x03, 0x04, 0x01, 0x02])
    assert decode_uint32(abcd, "ABCD") == 0x01020304
    assert decode_uint32(cdab, "CDAB") == 0x01020304


def test_decode_uint16_and_int16():
    assert decode_uint16(bytes([0x00, 0x2A])) == 42
    assert decode_int16(bytes([0xFF, 0xD8])) == -40


def test_maps():
    assert VOLUME_UNITS[2] == "mL"
    assert WORK_MODES[4] == "连续"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/instruments/test_tyd02_decode.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pump_monitor.instruments.leadfluid_tyd02'`

- [ ] **Step 3: Write minimal implementation**

`pump_monitor/instruments/leadfluid_tyd02.py`:
```python
from __future__ import annotations
import struct

VOLUME_UNITS = {0: "nL", 1: "uL", 2: "mL", 3: "L"}
WORK_MODES = {0: "仅注入", 1: "仅抽取", 2: "抽取/注入", 3: "注入/抽取", 4: "连续"}


def decode_ascii(raw: bytes) -> str:
    """Pump stores ASCII as 16-bit little-endian words: swap each byte pair."""
    sw = bytearray()
    i = 0
    while i + 1 < len(raw):
        sw.append(raw[i + 1])
        sw.append(raw[i])
        i += 2
    if i < len(raw):
        sw.append(raw[i])
    return bytes(sw).split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def _reorder32(raw: bytes, wordorder: str) -> bytes:
    if wordorder == "ABCD":
        return bytes(raw[:4])
    if wordorder == "CDAB":
        return bytes([raw[2], raw[3], raw[0], raw[1]])
    raise ValueError(f"bad wordorder {wordorder}")


def decode_float(raw: bytes, wordorder: str) -> float:
    return struct.unpack(">f", _reorder32(raw, wordorder))[0]


def decode_uint32(raw: bytes, wordorder: str) -> int:
    return struct.unpack(">I", _reorder32(raw, wordorder))[0]


def decode_uint16(raw: bytes) -> int:
    return struct.unpack(">H", raw[:2])[0]


def decode_int16(raw: bytes) -> int:
    return struct.unpack(">h", raw[:2])[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/instruments/test_tyd02_decode.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/instruments/leadfluid_tyd02.py tests/instruments/test_tyd02_decode.py
git commit -m "feat: leadfluid tyd02 register decoders"
```

---

### Task 7: TYD02Adapter (read_status, identity, word-order detect)

**Files:**
- Modify: `pump_monitor/instruments/leadfluid_tyd02.py` (append adapter)
- Test: `tests/instruments/test_tyd02_adapter.py`

**Interfaces:**
- Consumes: `ModbusClient.read_input_registers/read_holding_registers` (Task 3), decoders (Task 6), `StatusSnapshot` (Task 5).
- Produces: `TYD02Adapter(client, slave, wordorder)` with `identity()->str`, `read_status()->StatusSnapshot`, `detect_wordorder()->str`, and module function `derive_state(run,pause,dispense,alarm)->str`.

- [ ] **Step 1: Write the failing test**

`tests/instruments/test_tyd02_adapter.py`:
```python
import struct
import pytest
from pump_monitor.instruments.leadfluid_tyd02 import TYD02Adapter, derive_state


def f32(v, wo="CDAB"):
    b = struct.pack(">f", v)
    return bytes([b[2], b[3], b[0], b[1]]) if wo == "CDAB" else b


def u32(v, wo="CDAB"):
    b = struct.pack(">I", v)
    return bytes([b[2], b[3], b[0], b[1]]) if wo == "CDAB" else b


def u16(v):
    return struct.pack(">H", v)


def s16(v):
    return struct.pack(">h", v)


def enc_ascii(s: str) -> bytes:
    b = s.encode("ascii")
    sw = bytearray()
    i = 0
    while i + 1 < len(b):
        sw.append(b[i + 1]); sw.append(b[i]); i += 2
    if i < len(b):
        sw.append(0x00); sw.append(b[i])
    while len(sw) < 10:
        sw.append(0x00); sw.append(0x00)
    return bytes(sw[:10])


class FakeClient:
    def __init__(self, inp, hold):
        self.inp = inp
        self.hold = hold
    def read_input_registers(self, reg, count):
        return self.inp[(reg, count)]
    def read_holding_registers(self, reg, count):
        return self.hold[(reg, count)]


def _adapter():
    inp = {
        (1000, 1): s16(42),
        (1002, 2): f32(50.0),
        (1004, 4): u32(500) + u32(1000),       # cur=500 req=1000 -> 50%
        (1012, 1): u16(3),
        (1018, 5): bytes.fromhex("654c64616c4669750064"),   # "LeadFluid"
        (1023, 5): enc_ascii("TYD02"),
        (1032, 11): (f32(12.5) + u16(2) + f32(8.0) + u16(2)
                     + f32(4.5) + u16(2) + f32(30.0)),
        (1043, 4): u32(120000) + u32(60000),
        (1047, 1): u16(0),
    }
    hold = {
        (4017, 1): u16(0),    # 仅注入
        (4126, 1): u16(1),    # run
        (4024, 1): u16(0),    # not paused
        (4025, 1): u16(1),    # dispense active
    }
    return TYD02Adapter(FakeClient(inp, hold), slave=1, wordorder="CDAB")


def test_identity():
    a = _adapter()
    assert a.identity() == "LeadFluid TYD02"


def test_read_status_decodes_fields():
    s = _adapter().read_status()
    assert s.device_id == "LeadFluid TYD02"
    assert s.state == "running"
    assert s.work_mode == "仅注入"
    assert s.temp_c == 42.0
    assert s.flow_rpm == pytest.approx(50.0)
    assert s.progress_pct == 50.0
    assert s.cycles == 3
    assert s.acc_volume == pytest.approx(12.5) and s.acc_unit == "mL"
    assert s.consumed_volume == pytest.approx(8.0)
    assert s.remaining_volume == pytest.approx(4.5)
    assert s.elapsed_ms == 120000 and s.remaining_ms == 60000
    assert s.alarm is False


def test_derive_state_alarm_wins():
    assert derive_state(1, 1, 1, 1) == "alarm"
    assert derive_state(0, 1, 0, 0) == "paused"
    assert derive_state(1, 0, 0, 0) == "running"
    assert derive_state(0, 0, 0, 0) == "stopped"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/instruments/test_tyd02_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'TYD02Adapter'`

- [ ] **Step 3: Write minimal implementation**

Add to top of `pump_monitor/instruments/leadfluid_tyd02.py`:
```python
import time
from pump_monitor.instruments.base import StatusSnapshot
```

Append to `pump_monitor/instruments/leadfluid_tyd02.py`:
```python
def derive_state(run: int, pause: int, dispense: int, alarm: int) -> str:
    if alarm:
        return "alarm"
    if pause == 1:
        return "paused"
    if run == 1 or dispense == 1:
        return "running"
    return "stopped"


class TYD02Adapter:
    """Reads TYD02 status over Modbus into a StatusSnapshot. READ-ONLY."""

    REG_TEMP = 1000
    REG_INJECT_RPM = 1002          # float, 2 regs
    REG_CUR_STEPS = 1004           # u32 (cur steps) + next u32 (req steps)
    REG_CYCLES = 1012
    REG_COMPANY = 1018             # 5 regs
    REG_PRODUCT = 1023             # 5 regs
    REG_ACC_VOL = 1032             # float; block 1032..1042 (11 regs)
    REG_ELAPSED_MS = 1043          # u32 (elapsed) + u32 (remaining)
    REG_ALARM = 1047
    REG_MODE = 4017                # holding
    REG_RUN = 4126                 # holding
    REG_PAUSE = 4024               # holding
    REG_DISPENSE = 4025            # holding

    def __init__(self, client, slave: int = 1, wordorder: str = "CDAB"):
        self.client = client
        self.slave = slave
        self.wordorder = wordorder
        self._device_id = None

    def identity(self) -> str:
        if self._device_id is None:
            co = decode_ascii(self.client.read_input_registers(self.REG_COMPANY, 5))
            pr = decode_ascii(self.client.read_input_registers(self.REG_PRODUCT, 5))
            self._device_id = f"{co} {pr}".strip()
        return self._device_id

    def detect_wordorder(self) -> str:
        """Pick ABCD/CDAB by checking current<=required steps under each order."""
        raw = self.client.read_input_registers(self.REG_CUR_STEPS, 4)
        for order in ("CDAB", "ABCD"):
            cur = decode_uint32(raw[0:4], order)
            req = decode_uint32(raw[4:8], order)
            if 0 <= cur <= req:
                return order
        return self.wordorder

    def read_status(self) -> StatusSnapshot:
        c = self.client
        wo = self.wordorder
        temp = decode_int16(c.read_input_registers(self.REG_TEMP, 1))
        inject_rpm = decode_float(c.read_input_registers(self.REG_INJECT_RPM, 2), wo)
        steps = c.read_input_registers(self.REG_CUR_STEPS, 4)
        cur_steps = decode_uint32(steps[0:4], wo)
        req_steps = decode_uint32(steps[4:8], wo)
        cycles = decode_uint16(c.read_input_registers(self.REG_CYCLES, 1))
        vb = c.read_input_registers(self.REG_ACC_VOL, 11)
        acc = decode_float(vb[0:4], wo)
        acc_u = VOLUME_UNITS.get(decode_uint16(vb[4:6]), "")
        con = decode_float(vb[6:10], wo)
        con_u = VOLUME_UNITS.get(decode_uint16(vb[10:12]), "")
        rem = decode_float(vb[12:16], wo)
        rem_u = VOLUME_UNITS.get(decode_uint16(vb[16:18]), "")
        tb = c.read_input_registers(self.REG_ELAPSED_MS, 4)
        elapsed = decode_uint32(tb[0:4], wo)
        remaining = decode_uint32(tb[4:8], wo)
        alarm = decode_uint16(c.read_input_registers(self.REG_ALARM, 1))
        mode = decode_uint16(c.read_holding_registers(self.REG_MODE, 1))
        run = decode_uint16(c.read_holding_registers(self.REG_RUN, 1))
        pause = decode_uint16(c.read_holding_registers(self.REG_PAUSE, 1))
        dispense = decode_uint16(c.read_holding_registers(self.REG_DISPENSE, 1))
        progress = (100.0 * cur_steps / req_steps) if req_steps else None
        return StatusSnapshot(
            timestamp=time.time(),
            state=derive_state(run, pause, dispense, alarm),
            work_mode=WORK_MODES.get(mode, str(mode)),
            device_id=self.identity(),
            flow_rpm=inject_rpm,
            acc_volume=acc, acc_unit=acc_u,
            consumed_volume=con, consumed_unit=con_u,
            remaining_volume=rem, remaining_unit=rem_u,
            elapsed_ms=elapsed, remaining_ms=remaining,
            cycles=cycles, progress_pct=progress,
            temp_c=float(temp), alarm=bool(alarm),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/instruments/test_tyd02_adapter.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/instruments/leadfluid_tyd02.py tests/instruments/test_tyd02_adapter.py
git commit -m "feat: tyd02 adapter with register map + state derivation"
```

### Task 8: Sampler (background polling thread)

**Files:**
- Create: `pump_monitor/sampler.py`
- Test: `tests/test_sampler.py`

**Interfaces:**
- Consumes: an `InstrumentAdapter` (Task 5/7), `offline_snapshot` (Task 5).
- Produces: `Sampler(adapter, interval_s, on_sample)` with `start()/stop()`. Calls `on_sample(StatusSnapshot)` every `interval_s`; on adapter error emits an `offline` snapshot instead of crashing.

- [ ] **Step 1: Write the failing test**

`tests/test_sampler.py`:
```python
import time
from pump_monitor.sampler import Sampler
from pump_monitor.instruments.base import StatusSnapshot


class FakeAdapter:
    def __init__(self, seq):
        self.seq = list(seq)
    def identity(self):
        return "dev"
    def read_status(self):
        item = self.seq.pop(0)
        self.seq.append(item)            # cycle so it never runs dry
        if isinstance(item, Exception):
            raise item
        return item


def _snap(n):
    return StatusSnapshot(timestamp=time.time(), state="running",
                          work_mode="m", device_id="dev", temp_c=float(n))


def test_sampler_emits_samples():
    adapter = FakeAdapter([_snap(1), _snap(2)])
    out = []
    s = Sampler(adapter, interval_s=0.02, on_sample=out.append)
    s.start()
    time.sleep(0.12)
    s.stop()
    assert len(out) >= 2
    assert out[0].temp_c in (1.0, 2.0)


def test_sampler_emits_offline_on_error():
    adapter = FakeAdapter([RuntimeError("boom"), _snap(7)])
    out = []
    s = Sampler(adapter, interval_s=0.02, on_sample=out.append)
    s.start()
    time.sleep(0.12)
    s.stop()
    assert any(x.state == "offline" for x in out)
    assert any(x.temp_c == 7.0 for x in out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sampler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pump_monitor.sampler'`

- [ ] **Step 3: Write minimal implementation**

`pump_monitor/sampler.py`:
```python
from __future__ import annotations
import threading
import time
from typing import Callable, Optional
from pump_monitor.instruments.base import StatusSnapshot, offline_snapshot


class Sampler:
    """Polls an adapter on a fixed interval, pushing each snapshot to on_sample."""

    def __init__(self, adapter, interval_s: float,
                 on_sample: Callable[[StatusSnapshot], None]):
        self.adapter = adapter
        self.interval_s = interval_s
        self.on_sample = on_sample
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._device_id: Optional[str] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            self._device_id = self.adapter.identity()
        except Exception:
            self._device_id = "unknown"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                snap = self.adapter.read_status()
            except Exception as e:
                snap = offline_snapshot(self._device_id or "unknown", str(e))
            try:
                self.on_sample(snap)
            except Exception:
                pass
            elapsed = time.monotonic() - t0
            self._stop.wait(max(0.0, self.interval_s - elapsed))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sampler.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/sampler.py tests/test_sampler.py
git commit -m "feat: background sampler thread with error tolerance"
```

---

### Task 9: SessionLogger (CSV, research data archival)

Append-only per-session CSV with metadata comment header; flushed every row so a power loss never loses already-sampled data. Comment lines (`#`) precede the header; consumers skip them.

**Files:**
- Create: `pump_monitor/session_logger.py`
- Test: `tests/test_session_logger.py`

**Interfaces:**
- Consumes: `StatusSnapshot` (Task 5).
- Produces: `SessionLogger(log_dir)` with `start(meta)->path` (creates file, `.active=True`), `log(snap)` (no-op if inactive), `stop()`, attributes `.path`, `.active`.

- [ ] **Step 1: Write the failing test**

`tests/test_session_logger.py`:
```python
import csv
import os
import time
from pump_monitor.session_logger import SessionLogger
from pump_monitor.instruments.base import StatusSnapshot


def _snap(temp):
    return StatusSnapshot(timestamp=time.time(), state="running", work_mode="仅注入",
                          device_id="LeadFluid TYD02", acc_volume=12.5, acc_unit="mL",
                          temp_c=temp, flow_rpm=50.0, progress_pct=50.0)


def _rows(path):
    lines = [l for l in open(path, encoding="utf-8") if not l.startswith("#")]
    return list(csv.DictReader(lines))


def test_start_creates_file_with_metadata_and_header(tmp_path):
    log = SessionLogger(str(tmp_path))
    path = log.start({"experiment": "run1", "operator": "alice"})
    assert os.path.exists(path)
    txt = open(path, encoding="utf-8").read()
    assert "# experiment: run1" in txt
    assert "# operator: alice" in txt
    assert "timestamp_iso,state," in txt


def test_log_appends_rows(tmp_path):
    log = SessionLogger(str(tmp_path))
    log.start({"experiment": "run1"})
    log.log(_snap(41.0))
    log.log(_snap(42.0))
    log.stop()
    rows = _rows(log.path)
    assert len(rows) == 2
    assert rows[0]["temp_c"] == "41.0"
    assert rows[1]["acc_volume"] == "12.5"
    assert rows[0]["device_id"] == "LeadFluid TYD02"


def test_no_log_when_inactive(tmp_path):
    log = SessionLogger(str(tmp_path))
    log.log(_snap(40.0))
    assert log.active is False
    assert not os.listdir(tmp_path)


def test_filename_sanitized(tmp_path):
    log = SessionLogger(str(tmp_path))
    path = log.start({"experiment": "run/1 ?? bad"})
    base = os.path.basename(path)
    assert "/" not in base and " " not in base
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_session_logger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pump_monitor.session_logger'`

- [ ] **Step 3: Write minimal implementation**

`pump_monitor/session_logger.py`:
```python
from __future__ import annotations
import csv
import os
import time
from typing import Optional
from pump_monitor.instruments.base import StatusSnapshot


class SessionLogger:
    FIELDS = [
        "timestamp_iso", "state", "work_mode", "flow_rpm",
        "acc_volume", "acc_unit", "consumed_volume", "consumed_unit",
        "remaining_volume", "remaining_unit", "elapsed_ms", "remaining_ms",
        "cycles", "progress_pct", "temp_c", "alarm", "error_code", "device_id",
    ]

    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        self._file = None
        self._writer: Optional[csv.DictWriter] = None
        self.path: Optional[str] = None
        self.meta: dict = {}
        self.active = False

    def start(self, meta: dict) -> str:
        os.makedirs(self.log_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c for c in str(meta.get("experiment", "session"))
                       if c.isalnum() or c in "-_")[:24] or "session"
        self.path = os.path.join(self.log_dir, f"session_{stamp}_{safe}.csv")
        self.meta = dict(meta)
        self.meta["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._file = open(self.path, "a", newline="", encoding="utf-8")
        for k, v in self.meta.items():
            self._file.write(f"# {k}: {v}\n")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDS)
        self._writer.writeheader()
        self._file.flush()
        self.active = True
        return self.path

    def log(self, snap: StatusSnapshot) -> None:
        if not self.active or not self._writer:
            return

        def f(v):
            return "" if v is None else v

        row = {
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(snap.timestamp)),
            "state": snap.state, "work_mode": snap.work_mode,
            "flow_rpm": f(snap.flow_rpm),
            "acc_volume": f(snap.acc_volume), "acc_unit": snap.acc_unit,
            "consumed_volume": f(snap.consumed_volume), "consumed_unit": snap.consumed_unit,
            "remaining_volume": f(snap.remaining_volume), "remaining_unit": snap.remaining_unit,
            "elapsed_ms": f(snap.elapsed_ms), "remaining_ms": f(snap.remaining_ms),
            "cycles": f(snap.cycles), "progress_pct": f(snap.progress_pct),
            "temp_c": f(snap.temp_c), "alarm": int(snap.alarm),
            "error_code": f(snap.error_code), "device_id": snap.device_id,
        }
        self._writer.writerow(row)
        self._file.flush()

    def stop(self) -> None:
        self.active = False
        if self._file:
            self._file.close()
            self._file = None
            self._writer = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_session_logger.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/session_logger.py tests/test_session_logger.py
git commit -m "feat: append-only csv session logger"
```

### Task 10: Web app (Flask) + dashboard frontend

**Files:**
- Create: `pump_monitor/web/__init__.py` (empty), `pump_monitor/web/app.py`
- Create: `pump_monitor/web/static/index.html`, `pump_monitor/web/static/app.js`
- Test: `tests/web/__init__.py` (empty), `tests/web/test_app.py`

**Interfaces:**
- Consumes: an `InstrumentAdapter`, `SessionLogger` (Task 9), `Sampler` (Task 8), `StatusSnapshot`.
- Produces: `create_app(adapter, logger, interval_s) -> (Flask app, Sampler)`. Routes: `GET /` (dashboard), `GET /api/status`, `GET /api/stream` (SSE), `POST /api/session/start` (json meta), `POST /api/session/stop`, `GET /api/session/state`.

- [ ] **Step 1: Write the failing test**

`tests/web/test_app.py`:
```python
import time
from pump_monitor.web.app import create_app
from pump_monitor.session_logger import SessionLogger
from pump_monitor.instruments.base import StatusSnapshot


class StaticAdapter:
    def __init__(self, snap):
        self.snap = snap
    def identity(self):
        return self.snap.device_id
    def read_status(self):
        return self.snap


def _snap():
    return StatusSnapshot(timestamp=0, state="running", work_mode="仅注入",
                          device_id="LeadFluid TYD02", acc_volume=12.5,
                          acc_unit="mL", temp_c=42.0)


def test_status_returns_snapshot_after_poll(tmp_path):
    logger = SessionLogger(str(tmp_path))
    app, sampler = create_app(StaticAdapter(_snap()), logger, interval_s=0.05)
    try:
        client = app.test_client()
        time.sleep(0.12)
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.get_json()
        assert data["state"] == "running"
        assert data["acc_volume"] == 12.5
        assert data["device_id"] == "LeadFluid TYD02"
    finally:
        sampler.stop()


def test_session_start_then_stop(tmp_path):
    logger = SessionLogger(str(tmp_path))
    app, sampler = create_app(StaticAdapter(_snap()), logger, interval_s=0.05)
    try:
        client = app.test_client()
        r = client.post("/api/session/start", json={"experiment": "t1"})
        assert r.get_json()["ok"] is True
        assert logger.active is True
        assert client.get("/api/session/state").get_json()["active"] is True
        client.post("/api/session/stop")
        assert logger.active is False
    finally:
        sampler.stop()


def test_index_served(tmp_path):
    logger = SessionLogger(str(tmp_path))
    app, sampler = create_app(StaticAdapter(_snap()), logger, interval_s=0.05)
    try:
        client = app.test_client()
        r = client.get("/")
        assert r.status_code == 200
        assert b"TYD02" in r.data or b"monitor" in r.data.lower() or b"\xe7" in r.data
    finally:
        sampler.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/web/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pump_monitor.web'`

- [ ] **Step 3: Write minimal implementation**

`pump_monitor/web/app.py`:
```python
from __future__ import annotations
import json
import queue
import threading
from typing import List
from flask import Flask, Response, jsonify, request, send_from_directory

from pump_monitor.sampler import Sampler
from pump_monitor.session_logger import SessionLogger
from pump_monitor.instruments.base import StatusSnapshot


def _snapshot_to_dict(s: StatusSnapshot) -> dict:
    return {
        "timestamp": s.timestamp, "state": s.state, "work_mode": s.work_mode,
        "device_id": s.device_id, "flow_rpm": s.flow_rpm,
        "acc_volume": s.acc_volume, "acc_unit": s.acc_unit,
        "consumed_volume": s.consumed_volume, "consumed_unit": s.consumed_unit,
        "remaining_volume": s.remaining_volume, "remaining_unit": s.remaining_unit,
        "elapsed_ms": s.elapsed_ms, "remaining_ms": s.remaining_ms,
        "cycles": s.cycles, "progress_pct": s.progress_pct,
        "temp_c": s.temp_c, "alarm": s.alarm, "error_code": s.error_code,
    }


def create_app(adapter, logger: SessionLogger, interval_s: float):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    state = {"latest": None}
    state_lock = threading.Lock()
    clients: List[queue.Queue] = []
    clients_lock = threading.Lock()

    def on_sample(snap: StatusSnapshot):
        with state_lock:
            state["latest"] = snap
        payload = json.dumps(_snapshot_to_dict(snap))
        with clients_lock:
            dead = []
            for q in clients:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                clients.remove(q)
        if logger.active:
            logger.log(snap)

    sampler = Sampler(adapter, interval_s, on_sample)
    sampler.start()

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/status")
    def api_status():
        with state_lock:
            snap = state["latest"]
        if snap is None:
            return jsonify({"state": "offline", "device_id": ""})
        return jsonify(_snapshot_to_dict(snap))

    @app.get("/api/stream")
    def api_stream():
        q: queue.Queue = queue.Queue(maxsize=100)
        with clients_lock:
            clients.append(q)

        def gen():
            try:
                while True:
                    try:
                        yield f"data: {q.get(timeout=15)}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                with clients_lock:
                    if q in clients:
                        clients.remove(q)

        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/session/start")
    def session_start():
        meta = request.get_json(silent=True) or {}
        path = logger.start(meta)
        return jsonify({"ok": True, "path": path})

    @app.post("/api/session/stop")
    def session_stop():
        was = logger.active
        logger.stop()
        return jsonify({"ok": True, "was_active": was, "path": logger.path})

    @app.get("/api/session/state")
    def session_state():
        return jsonify({"active": logger.active, "path": logger.path})

    return app, sampler
```

`pump_monitor/web/static/index.html`:
```html
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Tyd02 注射泵监控台</title>
<style>
 body{font-family:-apple-system,"PingFang SC",sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:12px 16px;border-bottom:1px solid #222;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
 .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;padding:16px}
 .card{background:#1a1d24;border-radius:12px;padding:14px}
 .card .k{color:#8a93a3;font-size:12px}.card .v{font-size:20px;margin-top:4px}
 .state-running{color:#3ddc84}.state-alarm{color:#ff5c5c}
 .state-offline{color:#888}.state-paused{color:#f5c518}.state-stopped{color:#cfd3da}
 input,button{background:#262b34;color:#e6e6e6;border:1px solid #333;border-radius:8px;padding:6px 10px}
 button{cursor:pointer}#sess{color:#8a93a3}
</style>
</head>
<body>
<header>
 <b>Tyd02 注射泵监控台</b><span id="dev"></span><span style="flex:1"></span>
 <input id="exp" placeholder="实验名"><input id="op" placeholder="操作人">
 <button id="start">开始记录</button><button id="stop">停止记录</button><span id="sess"></span>
</header>
<div class="cards">
 <div class="card"><div class="k">状态</div><div class="v" id="state">-</div></div>
 <div class="card"><div class="k">工作模式</div><div class="v" id="mode">-</div></div>
 <div class="card"><div class="k">当前流速 (rpm)</div><div class="v" id="flow">-</div></div>
 <div class="card"><div class="k">累计液量</div><div class="v" id="acc">-</div></div>
 <div class="card"><div class="k">已输送</div><div class="v" id="con">-</div></div>
 <div class="card"><div class="k">剩余</div><div class="v" id="rem">-</div></div>
 <div class="card"><div class="k">已用时间</div><div class="v" id="elapsed">-</div></div>
 <div class="card"><div class="k">剩余时间</div><div class="v" id="remain">-</div></div>
 <div class="card"><div class="k">循环</div><div class="v" id="cyc">-</div></div>
 <div class="card"><div class="k">进度</div><div class="v" id="prog">-</div></div>
 <div class="card"><div class="k">温度 (℃)</div><div class="v" id="temp">-</div></div>
</div>
<canvas id="chart" height="120" style="margin:0 16px 16px"></canvas>
<script src="/static/app.js"></script>
</body>
</html>
```

`pump_monitor/web/static/app.js`:
```javascript
const $ = (id) => document.getElementById(id);
const num = (v, d=3) => (v === null || v === undefined) ? "-" : (+v).toFixed(d);
const fmtVol = (v, u) => (v === null || v === undefined) ? "-" : num(v,3) + (u ? " " + u : "");
const fmtMs = (ms) => (ms === null || ms === undefined) ? "-" : (ms >= 1000 ? (ms/1000).toFixed(0)+" s" : ms+" ms");

function render(s) {
  $("dev").textContent = s.device_id || "";
  const st = $("state"); st.textContent = s.state; st.className = "v state-" + (s.state || "offline");
  $("mode").textContent = s.work_mode || "-";
  $("flow").textContent = num(s.flow_rpm,3);
  $("acc").textContent = fmtVol(s.acc_volume, s.acc_unit);
  $("con").textContent = fmtVol(s.consumed_volume, s.consumed_unit);
  $("rem").textContent = fmtVol(s.remaining_volume, s.remaining_unit);
  $("elapsed").textContent = fmtMs(s.elapsed_ms);
  $("remain").textContent = fmtMs(s.remaining_ms);
  $("cyc").textContent = (s.cycles === null || s.cycles === undefined) ? "-" : s.cycles;
  $("prog").textContent = (s.progress_pct === null || s.progress_pct === undefined) ? "-" : num(s.progress_pct,1) + "%";
  $("temp").textContent = (s.temp_c === null || s.temp_c === undefined) ? "-" : num(s.temp_c,1);
  if (window.pushSample) window.pushSample(s);  // optional chart hook
}

async function pollOnce() {
  try { render(await (await fetch("/api/status")).json()); }
  catch (e) { $("state").textContent = "offline"; }
}
function startStream() {
  const es = new EventSource("/api/stream");
  es.onmessage = (ev) => { try { render(JSON.parse(ev.data)); } catch(e){} };
  es.onerror = () => { $("state").textContent = "offline"; };
}

$("start").onclick = async () => {
  const body = {experiment: $("exp").value || "session", operator: $("op").value};
  const j = await (await fetch("/api/session/start",
        {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)})).json();
  $("sess").textContent = "记录中: " + (j.path||"").split("/").pop();
};
$("stop").onclick = async () => {
  await fetch("/api/session/stop", {method:"POST"});
  $("sess").textContent = "已停止";
};

pollOnce(); startStream();
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/web/test_app.py -v`
Expected: PASS (3 tests). Full suite: `pytest -v`

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/web tests/web
git commit -m "feat: flask web app + sse + dashboard frontend"
```

---

### Task 11: Entry wiring + run (and optional chart)

**Files:**
- Create: `pump_monitor/__main__.py`
- Optional: `pump_monitor/web/static/chart.min.js` (vendored) + chart hook in `app.js`

**Interfaces:**
- Consumes: `load_config`, `SerialTransport`, `ModbusClient`, `TYD02Adapter`, `SessionLogger`, `create_app`.

- [ ] **Step 1: Write the entry module**

`pump_monitor/__main__.py`:
```python
from __future__ import annotations
import threading
import webbrowser

from pump_monitor.config import load_config
from pump_monitor.modbus_io import SerialTransport, ModbusClient
from pump_monitor.instruments.leadfluid_tyd02 import TYD02Adapter
from pump_monitor.session_logger import SessionLogger
from pump_monitor.web.app import create_app


def main():
    cfg = load_config()
    transport = SerialTransport(cfg.serial_port, cfg.baudrate, cfg.parity)
    transport.open()
    try:
        client = ModbusClient(transport, slave=cfg.slave_address)
        adapter = TYD02Adapter(client, slave=cfg.slave_address, wordorder=cfg.wordorder)
        logger = SessionLogger(cfg.log_dir)
        app, sampler = create_app(adapter, logger, cfg.sample_interval_ms / 1000.0)
        if cfg.auto_open_browser:
            url = f"http://127.0.0.1:{cfg.web_port}/"
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        app.run(host="127.0.0.1", port=cfg.web_port,
                threaded=True, use_reloader=False)
    finally:
        transport.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: (Optional) vendor Chart.js and wire a live chart**

Vendor (online once): `curl -L -o pump_monitor/web/static/chart.min.js https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js`
(Offline fallback: obtain `chart.umd.min.js` manually and place it at that path.)

Add to `index.html` before `<script src="/static/app.js">`:
```html
<script src="/static/chart.min.js"></script>
```

Prepend this chart hook to `pump_monitor/web/static/app.js` (it exposes `window.pushSample`, already called in `render`):
```javascript
(function () {
  if (typeof Chart === "undefined") { window.pushSample = null; return; }
  const ctx = document.getElementById("chart").getContext("2d");
  const data = [];
  const chart = new Chart(ctx, {
    type: "line",
    data: { labels: [], datasets: [{ label: "累计液量", data, borderColor: "#3ddc84", tension: 0.3 }] },
    options: { animation: false, scales: { x: { display: false } }, plugins: { legend: { labels: { color: "#cfd3da" } } } }
  });
  const t0 = Date.now();
  window.pushSample = function (s) {
    if (s.acc_volume === null || s.acc_volume === undefined) return;
    data.push(s.acc_volume);
    chart.data.labels.push(((Date.now() - t0) / 1000).toFixed(0));
    if (data.length > 300) { data.shift(); chart.data.labels.shift(); }
    chart.update("none");
  };
})();
```

- [ ] **Step 3: Run the full test suite + a live run**

Run: `pytest -v` → all tests PASS.
Live (pump connected, powered, transfer mode=计算机): `python -m pump_monitor`
Expected: browser opens `http://127.0.0.1:7800/`, dashboard shows live values; clicking 开始记录 writes rows to `data/sessions/session_*.csv`; 停止记录 closes the file. Validate state derivation (running/paused/stopped/alarm) against the pump's real panel — if a state reads wrong, calibrate `derive_state` (Task 7) against observed 4126/4024/4025/1047 values.

- [ ] **Step 4: Commit**

```bash
git add pump_monitor/__main__.py
git commit -m "feat: wire entry point + optional chart"
```

---

## Self-Review (performed)

**Spec coverage:** every spec requirement maps to a task — read-only polling (T7/T8), live dashboard (T10), CSV + session mgmt (T9, T10 endpoints), alarm highlight (T10 frontend colors by state), connection status (T5 `offline_snapshot` + T8/T10), config (T1), multi-instrument adapter (T5). Phase-2 write/control is intentionally absent (read-only constraint).

**Placeholders:** none in implementation steps. (Task 7 test originally used a `pytest_approx` workaround; fixed inline below to use `pytest.approx` directly.)

**Type consistency:** `derive_state(run,pause,dispense,alarm)`, `create_app(...) -> (app, sampler)`, `SessionLogger.active`, `Transport.write/read_wait/flush`, and all decoder names are used consistently across tasks.

**Assumptions flagged for live validation (Task 11 Step 3):**
- `derive_state` mapping (run/pause/dispense/alarm → state) needs calibration on the real pump.
- Float/uint32 byte order defaults to `CDAB` (config), with `TYD02Adapter.detect_wordorder()` as a runtime helper.
- Sampling at 0.5 s needs verifying against RS485 bus load; default is 1 s.

### Inline fix to Task 7 test

In `tests/instruments/test_tyd02_adapter.py`, the import line and float assertions must read `pytest.approx` (not `pytest_approx`):
- Top of file: `import struct` → `import struct\nimport pytest`.
- `s.flow_rpm == pytest_approx(50.0)` → `s.flow_rpm == pytest.approx(50.0)`.
- `s.acc_volume == pytest_approx(12.5)` → `s.acc_volume == pytest.approx(12.5)`.
- `s.consumed_volume == pytest_approx(8.0)` → `pytest.approx(8.0)`.
- `s.remaining_volume == pytest_approx(4.5)` → `pytest.approx(4.5)`.
- Remove the explanatory note about the workaround.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-06-30-syringe-pump-logger.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration. Uses `superpowers:subagent-driven-development`.
2. **Inline Execution** — execute tasks in this session with checkpoints. Uses `superpowers:executing-plans`.

