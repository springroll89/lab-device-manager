#!/usr/bin/env python3
"""Capture raw bytes + parsed frames from the Fangrui viscometer to CONFIRM the
remaining wire details: checksum algorithm, torque scaling, and whether the 2nd
frame type (D6 C8 16 35) appears. The 29-byte D6 C8 18 32 ... 13 AB layout,
9600 8N1 settings and active upload behavior were confirmed on an LVDV-2T.

Run with the device connected, powered, and running a viscosity test:
    python scripts/viscometer_capture.py [/dev/cu.usbserial-XXXX] [seconds]

For each captured D6 C8 18 32 frame it prints: hex, parsed physical values, and
checksum_rx vs the sum256 guess (OK/MISMATCH). If MISMATCH, the real checksum
algorithm differs and we adjust viscometer.py. Press Ctrl-C to stop early.

Note: parity defaults to NONE (8N1), NOT the pump's EVEN. The undocumented 2nd
frame type, if present, shows up as skipped D6 C8 bytes.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time
from lab_device_manager.config import load_config
from lab_device_manager.modbus_io import SerialTransport, TcpTransport
from lab_device_manager.instruments.viscometer import (
    checksum_sum256, extract_latest_frame, parse_viscometer_frame,
)

PORT = sys.argv[1] if len(sys.argv) > 1 else None
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0


def main():
    cfg = load_config()
    dev = next((item for item in cfg.devices if item.type == "viscometer"), None)
    if dev is None:
        raise SystemExit("no viscometer device configured")
    port = PORT or dev.serial_port
    baud = dev.baudrate if dev else 9600
    endpoint = port if PORT or dev.transport == "serial" else f"{dev.host}:{dev.tcp_port}"
    print(f"capturing {endpoint} @ {baud}, 8N1 (NONE parity), for {DURATION}s ...")
    tr = (
        TcpTransport(dev.host, dev.tcp_port, dev.connect_timeout_s)
        if not PORT and dev.transport == "tcp"
        else SerialTransport(port, baud, parity="NONE")
    )
    tr.open()
    buf = bytearray()
    n_frames = 0
    n_mismatch = 0
    deadline = time.monotonic() + DURATION
    try:
        while time.monotonic() < deadline:
            chunk = tr.read_wait(0.1)
            if chunk:
                buf.extend(chunk)
            frame, remaining = extract_latest_frame(bytes(buf))
            while frame is not None:
                n_frames += 1
                d = parse_viscometer_frame(frame)
                rx = frame[26]
                expected = checksum_sum256(frame)
                if rx != expected:
                    n_mismatch += 1
                print(f"[{n_frames}] hex={frame.hex(' ')}")
                print(f"     viscosity={d['viscosity_mPas']} mPa·s  temp={d['temperature_c']} C")
                print(f"     shear_rate={d['shear_rate_1s']} 1/s  "
                      f"shear_stress={d['shear_stress_mPa']} mPa "
                      f"({d['shear_stress_Pa']} Pa)  torque={d['torque_pct']}%")
                print(f"     checksum rx=0x{rx:02x} sum256=0x{expected:02x} "
                      f"[{'OK' if rx == expected else 'MISMATCH'}]")
                buf = bytearray(remaining)
                frame, remaining = extract_latest_frame(bytes(buf))
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        tr.close()
    print(f"\ncaptured {n_frames} frame(s); checksum mismatches: {n_mismatch}")
    if n_frames == 0:
        print("no frames seen. Raw tail (hex, first 200 chars):", bytes(buf).hex(' ')[:200])
        print("check: physical layer (RS232?), baud, parity, that the device is RUNNING a test.")


if __name__ == "__main__":
    main()
