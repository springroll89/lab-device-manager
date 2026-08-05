"""Read-only hot-plug discovery for TCP serial gateways and local adapters."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import hashlib
import re
import threading
import time
from typing import Callable, Iterable

from lab_device_manager.instruments.whd46 import enum_serial_ports
from lab_device_manager.runtime.engine import device_adapter_factory
from lab_device_manager.runtime.types import (
    DeviceConfig,
    DiscoveryConfig,
    GatewayDiscoveryConfig,
)


DEVICE_LABELS = {
    "viscometer": "粘度计",
    "stirrer": "搅拌器",
    "tyd02": "注射泵",
    "whd46": "温湿度控制器",
}

SERIAL_SETTINGS = {
    "viscometer": (9600, "NONE"),
    "stirrer": (9600, "NONE"),
    "tyd02": (9600, "EVEN"),
    "whd46": (9600, "NONE"),
}


def endpoint_key(config: DeviceConfig) -> str:
    if config.transport == "tcp":
        return f"tcp:{config.host}:{config.tcp_port}"
    return f"serial:{config.serial_port}"


def _safe_name(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    if text:
        return text
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


def _identified(adapter, device_type: str) -> bool:
    if device_type == "viscometer":
        snapshot = adapter.read_status()
        return (
            snapshot.state != "offline"
            and bool((snapshot.metrics or {}).get("data_verified"))
        )
    if device_type == "stirrer":
        identity = adapter.identity().strip().lower()
        return identity == "stirrer type 203"
    if device_type == "tyd02":
        identity = adapter.identity().strip().lower()
        return "tyd02" in identity or "leadfluid" in identity
    if device_type == "whd46":
        snapshot = adapter.read_status()
        channels = (snapshot.metrics or {}).get("channels") or []
        return snapshot.state != "offline" and len(channels) == 3
    return False


def probe_known_device(
    base: DeviceConfig,
    probe_types: Iterable[str],
) -> DeviceConfig | None:
    """Try known read-only signatures and return the first verified device."""
    for device_type in probe_types:
        baudrate, parity = SERIAL_SETTINGS[device_type]
        candidate = replace(
            base,
            name=f"auto-{device_type}-{_safe_name(endpoint_key(base))}",
            type=device_type,
            alias=DEVICE_LABELS[device_type],
            baudrate=baudrate,
            parity=parity,
            modbus_addr=1,
            wordorder="CDAB",
            auto_discovered=True,
        )
        cleanup = None
        try:
            adapter, cleanup = device_adapter_factory(candidate)
            if _identified(adapter, device_type):
                return candidate
        except Exception:
            pass
        finally:
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:
                    pass
    return None


def _gateway_candidates(
    gateway: GatewayDiscoveryConfig,
) -> list[DeviceConfig]:
    return [
        DeviceConfig(
            name=f"slot-{_safe_name(gateway.host)}-{port}",
            type="stirrer",
            transport="tcp",
            host=gateway.host,
            tcp_port=gateway.tcp_base_port + port,
            gateway_name=gateway.name,
            gateway_model=gateway.model,
            gateway_port=port,
            connect_timeout_s=0.35,
            auto_discovered=True,
        )
        for port in gateway.ports
    ]


class DiscoveryService:
    """Discover devices without assigning a fixed instrument to each port."""

    def __init__(
        self,
        engine,
        settings: DiscoveryConfig,
        *,
        probe: Callable[[DeviceConfig, Iterable[str]], DeviceConfig | None]
        = probe_known_device,
        serial_ports: Callable[[], list[dict[str, str]]] = enum_serial_ports,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.engine = engine
        self.settings = settings
        self.probe = probe
        self.serial_ports = serial_ports
        self.clock = clock
        self._stop = threading.Event()
        self._thread = None
        self._scan_lock = threading.Lock()
        self._dynamic: dict[str, tuple[int, float]] = {}

    def start(self) -> None:
        if not self.settings.enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.settings.scan_interval_s + 1))

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.scan_once()
            self._stop.wait(self.settings.scan_interval_s)

    def _release_missing(self, now: float) -> None:
        latest = self.engine.latest()
        for endpoint, (device_id, last_seen) in list(self._dynamic.items()):
            snapshot = latest.get(device_id)
            if snapshot is not None and snapshot.state != "offline":
                self._dynamic[endpoint] = (device_id, now)
                continue
            if now - last_seen < self.settings.forget_after_s:
                continue
            self.engine.remove_device(device_id, "comms_interrupted")
            self._dynamic.pop(endpoint, None)

    def _candidates(self) -> list[DeviceConfig]:
        result = []
        for gateway in self.settings.gateways:
            result.extend(_gateway_candidates(gateway))
        if self.settings.local_serial:
            for item in self.serial_ports():
                port = str(item.get("device", "")).strip()
                if not port:
                    continue
                result.append(
                    DeviceConfig(
                        name=f"slot-{_safe_name(port)}",
                        type="stirrer",
                        transport="serial",
                        serial_port=port,
                        auto_discovered=True,
                    )
                )
        return result

    def scan_once(self) -> None:
        if not self.settings.enabled or not self._scan_lock.acquire(False):
            return
        try:
            now = self.clock()
            self._release_missing(now)
            claimed = {
                endpoint_key(config)
                for config in self.engine.device_map().values()
            }
            candidates = [
                item for item in self._candidates()
                if endpoint_key(item) not in claimed
            ]
            if not candidates:
                return
            workers = min(8, len(candidates))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending = {
                    pool.submit(
                        self.probe,
                        candidate,
                        self.settings.probe_types,
                    ): candidate
                    for candidate in candidates
                }
                for future in as_completed(pending):
                    if self._stop.is_set():
                        break
                    try:
                        detected = future.result()
                    except Exception:
                        continue
                    if detected is None:
                        continue
                    endpoint = endpoint_key(detected)
                    if endpoint in self._dynamic:
                        continue
                    device_id = self.engine.add_device(detected)
                    self._dynamic[endpoint] = (device_id, now)
        finally:
            self._scan_lock.release()
