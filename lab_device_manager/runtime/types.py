from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceConfig:
    name: str
    type: str                 # adapter name: tyd02 / stirrer / viscometer
    alias: str = ""
    serial_port: str = ""
    baudrate: int = 9600
    parity: str = "EVEN"
    modbus_addr: int = 1
    wordorder: str = "CDAB"
    channel: int = 1
