from __future__ import annotations
import os
import pathlib
import secrets
import tomllib
from dataclasses import dataclass
from urllib.parse import urlparse
from lab_device_manager.runtime.types import (
    DeviceConfig,
    DiscoveryConfig,
    GatewayDiscoveryConfig,
)


def _load_or_create_secret(path: str = "./data/.session_secret") -> str:
    """Load or generate a persistent Flask session secret.

    The secret is stored outside version control (under data/) so it survives
    restarts but is not committed.
    """
    p = pathlib.Path(path)
    if p.exists():
        os.chmod(p, 0o600)
        return p.read_text().strip()
    s = secrets.token_urlsafe(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s)
    os.chmod(p, 0o600)
    return s


@dataclass(frozen=True)
class Config:
    db_path: str = "./data/lab_device_manager.db"
    sample_interval_ms: int = 1000
    web_port: int = 7800
    web_host: str = "0.0.0.0"
    public_base_url: str = ""
    tls_certfile: str = ""
    tls_keyfile: str = ""
    auto_open_browser: bool = True
    topology_collector_name: str = "后台采集主机"
    topology_switch_name: str = "网络汇聚设备"
    topology_switch_model: str = ""
    devices: tuple = ()   # tuple[DeviceConfig]
    discovery: DiscoveryConfig = DiscoveryConfig()
    secret_key: str = ""


def load_config(path: str | None = None) -> Config:
    if path is None:
        local_path = pathlib.Path("config.local.toml")
        path = str(local_path if local_path.exists() else "config.toml")
    with open(path, "rb") as f:
        d = tomllib.load(f)
    sample_interval_ms = d.get("sample_interval_ms", 1000)
    if not isinstance(sample_interval_ms, int) or sample_interval_ms <= 0:
        raise ValueError("config: sample_interval_ms must be a positive integer")
    if "devices" in d and not isinstance(d["devices"], list):
        raise ValueError("config: use [[devices]] (array of tables), not [devices]")
    devs = tuple(DeviceConfig(
        name=dev["name"], type=dev["type"], alias=dev.get("alias", ""),
        transport=dev.get("transport", "serial").lower(),
        serial_port=dev.get("serial_port", ""), baudrate=dev.get("baudrate", 9600),
        host=dev.get("host", ""), tcp_port=dev.get("tcp_port", 0),
        gateway_name=str(dev.get("gateway_name", "")).strip(),
        gateway_model=str(dev.get("gateway_model", "")).strip(),
        gateway_port=dev.get("gateway_port", 0),
        connect_timeout_s=dev.get("connect_timeout_s", 0.5),
        parity=dev.get("parity", "EVEN"), modbus_addr=dev.get("modbus_addr", 1),
        wordorder=dev.get("wordorder", "CDAB"), channel=dev.get("channel", 1),
        auto_discovered=bool(dev.get("auto_discovered", False)),
    ) for dev in d.get("devices", []))
    for dev in devs:
        if dev.transport not in {"serial", "tcp"}:
            raise ValueError(
                f"config: device {dev.name!r} transport must be serial or tcp"
            )
        if dev.transport == "tcp" and (
            not dev.host or not 1 <= dev.tcp_port <= 65535
        ):
            raise ValueError(
                f"config: TCP device {dev.name!r} requires host and tcp_port"
            )
        if not isinstance(dev.gateway_port, int) or dev.gateway_port < 0:
            raise ValueError(
                f"config: device {dev.name!r} gateway_port must be a nonnegative integer"
            )
    raw_secret = d.get("secret_key", "")
    secret_key = raw_secret if raw_secret else _load_or_create_secret()
    tls_certfile = str(d.get("tls_certfile", "")).strip()
    tls_keyfile = str(d.get("tls_keyfile", "")).strip()
    if bool(tls_certfile) != bool(tls_keyfile):
        raise ValueError(
            "config: tls_certfile and tls_keyfile must be configured together"
        )
    public_base_url = str(
        d.get("public_base_url", "")
    ).strip().rstrip("/")
    if public_base_url:
        parsed = urlparse(public_base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "config: public_base_url must be an http(s) origin"
            )
        if tls_certfile and parsed.scheme != "https":
            raise ValueError(
                "config: public_base_url must use https when TLS is enabled"
            )
    topology = d.get("topology") or {}
    if not isinstance(topology, dict):
        raise ValueError("config: topology must be a table")
    discovery_raw = d.get("discovery") or {}
    if not isinstance(discovery_raw, dict):
        raise ValueError("config: discovery must be a table")
    gateway_items = discovery_raw.get("gateways", [])
    if not isinstance(gateway_items, list):
        raise ValueError(
            "config: use [[discovery.gateways]] for gateway discovery"
        )
    gateways = []
    for item in gateway_items:
        if not isinstance(item, dict):
            raise ValueError("config: discovery gateway must be a table")
        host = str(item.get("host", "")).strip()
        name = str(item.get("name", "")).strip()
        raw_ports = item.get("ports", [1, 2, 3, 4])
        if not host or not name:
            raise ValueError(
                "config: discovery gateway requires name and host"
            )
        if (
            not isinstance(raw_ports, list)
            or not raw_ports
            or any(
                not isinstance(port, int) or not 1 <= port <= 4
                for port in raw_ports
            )
        ):
            raise ValueError(
                "config: discovery gateway ports must contain 1..4"
            )
        base_port = item.get("tcp_base_port", 4000)
        if not isinstance(base_port, int) or not 1 <= base_port <= 65531:
            raise ValueError(
                "config: discovery gateway tcp_base_port is invalid"
            )
        gateways.append(
            GatewayDiscoveryConfig(
                name=name,
                host=host,
                model=str(item.get("model", "UT-6804")).strip()
                or "UT-6804",
                ports=tuple(dict.fromkeys(raw_ports)),
                tcp_base_port=base_port,
            )
        )
    allowed_probe_types = {"viscometer", "stirrer", "tyd02", "whd46"}
    raw_probe_types = discovery_raw.get(
        "probe_types",
        ["viscometer", "stirrer", "tyd02", "whd46"],
    )
    if (
        not isinstance(raw_probe_types, list)
        or not raw_probe_types
        or any(item not in allowed_probe_types for item in raw_probe_types)
    ):
        raise ValueError("config: discovery probe_types contains unknown type")
    scan_interval_s = discovery_raw.get("scan_interval_s", 5.0)
    forget_after_s = discovery_raw.get("forget_after_s", 15.0)
    if not isinstance(scan_interval_s, (int, float)) or scan_interval_s <= 0:
        raise ValueError("config: discovery scan_interval_s must be positive")
    if not isinstance(forget_after_s, (int, float)) or forget_after_s <= 0:
        raise ValueError("config: discovery forget_after_s must be positive")
    discovery = DiscoveryConfig(
        enabled=bool(discovery_raw.get("enabled", False)),
        scan_interval_s=float(scan_interval_s),
        forget_after_s=float(forget_after_s),
        local_serial=bool(discovery_raw.get("local_serial", False)),
        probe_types=tuple(dict.fromkeys(raw_probe_types)),
        gateways=tuple(gateways),
    )
    return Config(
        db_path=d.get("db_path", "./data/lab_device_manager.db"),
        sample_interval_ms=sample_interval_ms,
        web_port=d.get("web_port", 7800),
        web_host=d.get("web_host", "0.0.0.0"),
        public_base_url=public_base_url,
        tls_certfile=tls_certfile,
        tls_keyfile=tls_keyfile,
        auto_open_browser=d.get("auto_open_browser", True),
        topology_collector_name=str(
            topology.get("collector_name", "后台采集主机")
        ).strip() or "后台采集主机",
        topology_switch_name=str(
            topology.get("switch_name", "网络汇聚设备")
        ).strip() or "网络汇聚设备",
        topology_switch_model=str(
            topology.get("switch_model", "")
        ).strip(),
        devices=devs,
        discovery=discovery,
        secret_key=secret_key,
    )
