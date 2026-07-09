from __future__ import annotations
from lab_device_manager.modbus_io import ModbusClient
from lab_device_manager.instruments.leadfluid_tyd02 import TYD02Adapter
from lab_device_manager.instruments.stirrer import StirrerAdapter
from lab_device_manager.instruments.viscometer import ViscometerAdapter
from lab_device_manager.instruments.whd46_33 import WHD46Adapter


def make_adapter(name: str, transport, slave: int = 1, wordorder: str = "CDAB",
                 read_timeout: float = 0.5):
    """Select an instrument adapter by config name. Default: tyd02 (syringe pump).
    Modbus instruments (tyd02, stirrer, whd46) wrap `transport` in a ModbusClient; the
    viscometer takes the raw transport (custom streaming protocol)."""
    n = (name or "tyd02").lower()
    if n == "tyd02":
        return TYD02Adapter(ModbusClient(transport, slave=slave), wordorder=wordorder)
    if n == "stirrer":
        return StirrerAdapter(ModbusClient(transport, slave=slave))
    if n == "viscometer":
        return ViscometerAdapter(transport, read_timeout=read_timeout)
    if n == "whd46":
        return WHD46Adapter(ModbusClient(transport, slave=slave))
    raise ValueError(f"unknown instrument {name!r} (expected 'tyd02', 'stirrer', 'viscometer', or 'whd46')")
