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
    auto_discovered: bool = False


@dataclass(frozen=True)
class GatewayDiscoveryConfig:
    name: str
    host: str
    model: str = "UT-6804"
    ports: tuple[int, ...] = (1, 2, 3, 4)
    tcp_base_port: int = 4000


@dataclass(frozen=True)
class DiscoveryConfig:
    enabled: bool = False
    scan_interval_s: float = 5.0
    forget_after_s: float = 15.0
    local_serial: bool = False
    probe_types: tuple[str, ...] = (
        "viscometer",
        "stirrer",
        "tyd02",
        "whd46",
    )
    gateways: tuple[GatewayDiscoveryConfig, ...] = ()
