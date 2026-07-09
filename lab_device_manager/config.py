from __future__ import annotations
import pathlib
import secrets
import tomllib
from dataclasses import dataclass
from lab_device_manager.runtime.types import DeviceConfig


def _load_or_create_secret(path: str = "./data/.session_secret") -> str:
    """Load or generate a persistent Flask session secret.

    The secret is stored outside version control (under data/) so it survives
    restarts but is not committed.
    """
    p = pathlib.Path(path)
    if p.exists():
        return p.read_text().strip()
    s = secrets.token_urlsafe(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s)
    return s


@dataclass(frozen=True)
class Config:
    db_path: str = "./data/lab_device_manager.db"
    sample_interval_ms: int = 1000
    web_port: int = 7800
    auto_open_browser: bool = True
    devices: tuple = ()   # tuple[DeviceConfig]
    secret_key: str = ""
    login_password: str = ""


def load_config(path: str = "config.toml") -> Config:
    with open(path, "rb") as f:
        d = tomllib.load(f)
    if "devices" in d and not isinstance(d["devices"], list):
        raise ValueError("config: use [[devices]] (array of tables), not [devices]")
    devs = tuple(DeviceConfig(
        name=dev["name"], type=dev["type"], alias=dev.get("alias", ""),
        serial_port=dev.get("serial_port", ""), baudrate=dev.get("baudrate", 9600),
        parity=dev.get("parity", "EVEN"), modbus_addr=dev.get("modbus_addr", 1),
        wordorder=dev.get("wordorder", "CDAB"), channel=dev.get("channel", 1),
    ) for dev in d.get("devices", []))
    raw_secret = d.get("secret_key", "")
    secret_key = raw_secret if raw_secret else _load_or_create_secret()
    return Config(
        db_path=d.get("db_path", "./data/lab_device_manager.db"),
        sample_interval_ms=d.get("sample_interval_ms", 1000),
        web_port=d.get("web_port", 7800),
        auto_open_browser=d.get("auto_open_browser", True),
        devices=devs,
        secret_key=secret_key,
        login_password=d.get("login_password", ""),
    )
