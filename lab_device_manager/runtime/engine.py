from __future__ import annotations
import sys
import threading
from typing import Callable, Optional
from lab_device_manager.sampler import Sampler
from lab_device_manager.instruments.factory import make_adapter
from lab_device_manager.modbus_io import SerialTransport
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
        self._samplers = []
        self._cleanups = []
        self._device_map = {}
        self._adapters = {}
        self._extra_adapters = {}

    def start(self):
        for dc in self.devices:
            try:
                device_id = self.repo.upsert_device(dc.name, dc.type, dc.alias)
                self._device_map[device_id] = dc
                adapter, cleanup = self.adapter_factory(dc)
                self._adapters[device_id] = adapter
                self._cleanups.append(cleanup)
                det = RunDetector(device_id, dc.channel, self.repo, on_event=self.on_event)
                latest, lock = self._latest, self._latest_lock

                def on_sample(snap, did=device_id, det=det):
                    det.on_sample(snap)
                    with lock:
                        latest[did] = snap

                sampler = Sampler(adapter, self.interval, on_sample)
                sampler.start()
                self._samplers.append(sampler)
            except Exception as e:
                # One unreachable device (e.g. unplugged, wrong port) must NOT crash the
                # whole app: register it as offline and keep serving the rest.
                try:
                    device_id = self.repo.upsert_device(dc.name, dc.type, dc.alias)
                    self._device_map[device_id] = dc
                    self._latest[device_id] = offline_snapshot(dc.alias or dc.name, str(e))
                except Exception:
                    pass
                print(f"[engine] {dc.name} offline, skipped: {e}", file=sys.stderr)

    def stop(self):
        for s in self._samplers:
            s.stop()
        for c in self._cleanups:
            try:
                c()
            except Exception:
                pass

    def latest(self) -> dict:
        with self._latest_lock:
            return dict(self._latest)

    def device_map(self) -> dict:
        return dict(self._device_map)

    def _get_adapter(self, device_id: int):
        """获取指定设备的适配器"""
        if device_id not in self._device_map:
            return None
        return self._extra_adapters.get(device_id) or self._adapters.get(device_id)


def serial_adapter_factory(dc: DeviceConfig) -> tuple:
    """Production adapter factory: opens a SerialTransport and builds the adapter.
    Returns (adapter, cleanup) where cleanup closes the transport."""
    transport = SerialTransport(dc.serial_port, dc.baudrate, dc.parity)
    transport.open()
    adapter = make_adapter(dc.type, transport, slave=dc.modbus_addr, wordorder=dc.wordorder)
    return adapter, transport.close
