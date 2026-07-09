#!/usr/bin/env python3
"""Sweep baud x parity x slave-addr to discover the HMS-C stirrer's serial params.
Success = holding register 40001 (machine type) reads back 203.
Run with the stirrer powered & its serial line connected:
    python scripts/stirrer_probe.py [/dev/cu.usbserial-XXXX]
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lab_device_manager.config import load_config
from lab_device_manager.modbus_io import SerialTransport, ModbusClient

_cfg = load_config()
PORT = sys.argv[1] if len(sys.argv) > 1 else (_cfg.devices[0].serial_port if _cfg.devices else "")
BAUDS = [9600, 19200, 4800, 38400, 115200]
PARITIES = ["NONE", "EVEN", "ODD"]   # 8N1 / 8E1 / 8O1
ADDRS = [1, 2, 3, 16]
EXPECT = 203


def try_config(port, baud, parity, addr):
    try:
        tr = SerialTransport(port, baud, parity)
        tr.open()
    except OSError:
        return None
    try:
        c = ModbusClient(tr, slave=addr, read_timeout=0.3, gap_timeout=0.05, pre_silence_s=0.004)
        raw = c.read_holding_registers(0, 1)        # 40001 machine type
        return (raw[0] << 8) | raw[1]               # big-endian uint16
    except Exception:
        return None
    finally:
        tr.close()


def main():
    print(f"sweeping {PORT} for stirrer (expect machine type {EXPECT})...")
    found = []
    for parity in PARITIES:
        for baud in BAUDS:
            for addr in ADDRS:
                val = try_config(PORT, baud, parity, addr)
                if val == EXPECT:
                    print(f"  ✓ HIT  baud={baud} parity={parity} addr={addr} -> {val}")
                    found.append((baud, parity, addr))
    if found:
        b, p, a = found[0]
        print(f'\n=> set in config.toml: instrument="stirrer" baudrate={b} parity="{p}" slave_address={a}')
    else:
        print("\n✗ no config returned 203. Check: physical layer (manual says RS485 port C), "
              "wiring A/B, that the device is the stirrer, and that it's powered.")


if __name__ == "__main__":
    main()
