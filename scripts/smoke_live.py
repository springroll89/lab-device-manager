#!/usr/bin/env python3
"""Manual hardware smoke test for serial transport + framing.
Run with the pump powered & connected:  python scripts/smoke_live.py
Expected: prints raw company-info bytes '65 4c 64 61 6c 46 69 75 00 64'."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lab_device_manager.config import load_config
from lab_device_manager.modbus_io import SerialTransport, ModbusClient


def main():
    cfg = load_config()
    port = sys.argv[1] if len(sys.argv) > 1 else (cfg.devices[0].serial_port if cfg.devices else "")
    dev = cfg.devices[0] if cfg.devices else None
    if dev is None:
        print("no devices configured; pass a port as argv[1]")
        sys.exit(1)
    tr = SerialTransport(port, dev.baudrate, dev.parity)
    tr.open()
    try:
        c = ModbusClient(tr, slave=dev.modbus_addr)
        raw = c.read_input_registers(1018, 5)  # company info @1018, 5 regs
        print("company raw:", raw.hex(" "))
    finally:
        tr.close()


if __name__ == "__main__":
    main()
