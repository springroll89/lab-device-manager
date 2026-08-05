from __future__ import annotations
from dataclasses import replace
import sys
import threading
import time
from typing import Callable, Optional
from lab_device_manager.sampler import Sampler
from lab_device_manager.instruments.factory import make_adapter
from lab_device_manager.modbus_io import SerialTransport, TcpTransport
from lab_device_manager.instruments.base import StatusSnapshot, offline_snapshot
from lab_device_manager.runtime.run_detector import RunDetector
from lab_device_manager.runtime.types import DeviceConfig


class Engine:
    """Wires one Sampler + RunDetector per device; writes runs/events/samples to repo,
    keeps a latest-snapshot store (for the future dashboard)."""

    def __init__(self, repo, devices, sample_interval_s: float,
                 adapter_factory: Callable[[DeviceConfig], tuple],
                 on_event: Optional[Callable[[dict], None]] = None):
        self.repo = repo
        self.devices = devices
        self.interval = sample_interval_s
        self.adapter_factory = adapter_factory
        self.on_event = on_event
        self._latest = {}
        self._latest_lock = threading.Lock()
        self._device_lock = threading.RLock()
        self._samplers = {}
        self._cleanups = {}
        self._device_map = {}
        self._adapters = {}
        self._detectors = {}
        self._started = False

    def start(self):
        if not self._started:
            self.repo.close_all_open_runs(
                int(time.time() * 1000), "interrupted_restart"
            )
            self._started = True
        for dc in list(self.devices):
            self.add_device(dc)

    def stop(self):
        with self._device_lock:
            for device_id in list(self._device_map):
                self._stop_device(device_id, "interrupted_shutdown")
            self._started = False

    def _start_device(self, device_id: int, config: DeviceConfig):
        adapter, cleanup = self.adapter_factory(config)
        detector = self._detectors.get(device_id)
        if detector is None:
            detector = RunDetector(
                device_id,
                config.channel,
                self.repo,
                on_event=self.on_event,
            )
            self._detectors[device_id] = detector

        def on_sample(snap, did=device_id, det=detector):
            det.on_sample(snap)
            with self._latest_lock:
                self._latest[did] = snap

        sampler = Sampler(adapter, self.interval, on_sample)
        self._adapters[device_id] = adapter
        self._cleanups[device_id] = cleanup
        self._samplers[device_id] = sampler
        try:
            sampler.start()
        except Exception:
            self._samplers.pop(device_id, None)
            self._adapters.pop(device_id, None)
            self._cleanups.pop(device_id, None)
            try:
                cleanup()
            except Exception:
                pass
            raise

    def _stop_device(
        self, device_id: int, end_status: str = "interrupted_reconnect"
    ):
        sampler = self._samplers.pop(device_id, None)
        if sampler is not None:
            sampler.stop()
        detector = self._detectors.get(device_id)
        if detector is not None:
            detector.finalize(end_status)
        cleanup = self._cleanups.pop(device_id, None)
        self._adapters.pop(device_id, None)
        if cleanup is not None:
            try:
                cleanup()
            except Exception:
                pass

    def _set_offline(
        self,
        device_id: int,
        config: DeviceConfig,
        reason: str,
    ):
        with self._latest_lock:
            self._latest[device_id] = offline_snapshot(
                config.alias or config.name,
                reason,
                communication_status="gateway_offline",
            )

    def add_device(self, config: DeviceConfig) -> int:
        """Register and start one statically configured or discovered device."""
        with self._device_lock:
            device_id = self.repo.upsert_device(
                config.name,
                config.type,
                config.alias,
            )
            if device_id in self._device_map:
                return device_id
            self._device_map[device_id] = config
            if all(item.name != config.name for item in self.devices):
                self.devices.append(config)
            try:
                self._start_device(device_id, config)
            except Exception as exc:
                self._set_offline(device_id, config, str(exc))
                print(
                    f"[engine] {config.name} offline, skipped: {exc}",
                    file=sys.stderr,
                )
            return device_id

    def remove_device(
        self,
        device_id: int,
        end_status: str = "comms_interrupted",
    ) -> bool:
        """Stop a discovered device without deleting its historical records."""
        with self._device_lock:
            config = self._device_map.get(device_id)
            if config is None:
                return False
            self._stop_device(device_id, end_status)
            self._device_map.pop(device_id, None)
            self._detectors.pop(device_id, None)
            self.devices[:] = [
                item for item in self.devices if item.name != config.name
            ]
            with self._latest_lock:
                self._latest.pop(device_id, None)
            return True

    def reconnect_device(self, device_id: int, serial_port: str) -> dict:
        """让 Engine 重新取得设备连接所有权并恢复统一采样。"""
        with self._device_lock:
            config = self._device_map.get(device_id)
            if config is None:
                return {"ok": False, "error": "device not found"}
            updated = replace(config, serial_port=serial_port)
            self._stop_device(device_id, "interrupted_reconnect")
            self._device_map[device_id] = updated
            try:
                self._start_device(device_id, updated)
            except Exception as exc:
                self._set_offline(device_id, updated, str(exc))
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "port": serial_port}

    def disconnect_device(self, device_id: int) -> dict:
        """停止指定设备的统一采样并释放串口。"""
        with self._device_lock:
            config = self._device_map.get(device_id)
            if config is None:
                return {"ok": False, "error": "device not found"}
            self._stop_device(device_id, "manual_disconnect")
            self._set_offline(device_id, config, "manual disconnect")
            return {"ok": True}

    def latest(self) -> dict:
        with self._latest_lock:
            return dict(self._latest)

    def device_map(self) -> dict:
        with self._device_lock:
            return dict(self._device_map)

    def _get_adapter(self, device_id: int):
        """获取指定设备的适配器"""
        with self._device_lock:
            if device_id not in self._device_map:
                return None
            return self._adapters.get(device_id)


def device_adapter_factory(dc: DeviceConfig) -> tuple:
    """Open the configured serial or TCP transport and build its adapter.

    TCP mode targets a serial server's transparent-transmission channel. Its
    transport reconnects lazily so devices recover without restarting the app.
    Returns (adapter, cleanup) where cleanup closes the transport."""
    if dc.transport == "tcp":
        transport = TcpTransport(
            dc.host,
            dc.tcp_port,
            connect_timeout=dc.connect_timeout_s,
        )
    else:
        transport = SerialTransport(dc.serial_port, dc.baudrate, dc.parity)
    transport.open()
    try:
        adapter = make_adapter(
            dc.type,
            transport,
            slave=dc.modbus_addr,
            wordorder=dc.wordorder,
        )
    except Exception:
        transport.close()
        raise
    return adapter, transport.close


# Kept for callers that still import the former production-factory name.
serial_adapter_factory = device_adapter_factory
