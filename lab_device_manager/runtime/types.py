from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceConfig:
    name: str
    type: str                 # adapter name: tyd02 / stirrer / viscometer
    alias: str = ""
    transport: str = "serial"     # serial | tcp
    serial_port: str = ""
    host: str = ""
    tcp_port: int = 0
    gateway_name: str = ""
    gateway_model: str = ""
    gateway_port: int = 0
    connect_timeout_s: float = 0.5
    baudrate: int = 9600
    parity: str = "EVEN"
    modbus_addr: int = 1
    wordorder: str = "CDAB"
    channel: int = 1
