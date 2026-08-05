from dataclasses import replace
import time

from lab_device_manager.instruments.base import StatusSnapshot, offline_snapshot
from lab_device_manager.runtime.discovery import DiscoveryService, endpoint_key
from lab_device_manager.runtime.types import (
    DeviceConfig,
    DiscoveryConfig,
    GatewayDiscoveryConfig,
)


def _online_snapshot():
    return StatusSnapshot(
        timestamp=time.time(),
        state="stopped",
        work_mode="",
        device_id="detected",
    )


class FakeEngine:
    def __init__(self, configs=()):
        self._configs = {
            index: config for index, config in enumerate(configs, start=1)
        }
        self._latest = {
            device_id: _online_snapshot() for device_id in self._configs
        }
        self.added = []
        self.removed = []

    def device_map(self):
        return dict(self._configs)

    def latest(self):
        return dict(self._latest)

    def add_device(self, config):
        device_id = max(self._configs, default=0) + 1
        self._configs[device_id] = config
        self._latest[device_id] = _online_snapshot()
        self.added.append(config)
        return device_id

    def remove_device(self, device_id, end_status):
        self.removed.append((device_id, end_status))
        self._configs.pop(device_id, None)
        self._latest.pop(device_id, None)
        return True


def _settings(**overrides):
    values = {
        "enabled": True,
        "scan_interval_s": 5,
        "forget_after_s": 15,
        "local_serial": True,
        "probe_types": ("viscometer", "stirrer", "tyd02", "whd46"),
        "gateways": (
            GatewayDiscoveryConfig(
                name="UT-6804-01",
                host="192.168.1.125",
                ports=(1, 2),
            ),
        ),
    }
    values.update(overrides)
    return DiscoveryConfig(**values)


def test_scans_gateway_and_local_ports_and_adds_only_verified_devices():
    engine = FakeEngine()

    def probe(base, _types):
        if base.tcp_port == 4002:
            return replace(
                base,
                name="auto-stirrer-port-2",
                type="stirrer",
                alias="搅拌器",
            )
        if base.serial_port == "COM7":
            return replace(
                base,
                name="auto-tyd02-com7",
                type="tyd02",
                alias="注射泵",
            )
        return None

    discovery = DiscoveryService(
        engine,
        _settings(),
        probe=probe,
        serial_ports=lambda: [{"device": "COM7", "desc": "USB Serial"}],
    )

    discovery.scan_once()

    assert {
        (item.type, endpoint_key(item)) for item in engine.added
    } == {
        ("stirrer", "tcp:192.168.1.125:4002"),
        ("tyd02", "serial:COM7"),
    }


def test_does_not_probe_an_endpoint_already_owned_by_engine():
    configured = DeviceConfig(
        name="existing",
        type="stirrer",
        transport="tcp",
        host="192.168.1.125",
        tcp_port=4001,
    )
    engine = FakeEngine([configured])
    probed = []

    def probe(base, _types):
        probed.append(endpoint_key(base))
        return None

    discovery = DiscoveryService(
        engine,
        _settings(local_serial=False),
        probe=probe,
    )

    discovery.scan_once()

    assert "tcp:192.168.1.125:4001" not in probed
    assert "tcp:192.168.1.125:4002" in probed


def test_removes_discovered_device_after_disconnect_grace_period():
    engine = FakeEngine()
    now = [100.0]
    detect = [True]

    def probe(base, _types):
        if not detect[0] or base.tcp_port != 4001:
            return None
        return replace(
            base,
            name="auto-stirrer-port-1",
            type="stirrer",
            alias="搅拌器",
        )

    discovery = DiscoveryService(
        engine,
        _settings(
            local_serial=False,
            forget_after_s=10,
            gateways=(
                GatewayDiscoveryConfig(
                    name="UT-6804-01",
                    host="192.168.1.125",
                    ports=(1,),
                ),
            ),
        ),
        probe=probe,
        clock=lambda: now[0],
    )
    discovery.scan_once()
    device_id = next(iter(engine._configs))
    engine._latest[device_id] = offline_snapshot(
        "stirrer",
        "no response",
        communication_status="instrument_unresponsive",
    )
    detect[0] = False

    now[0] = 109.0
    discovery.scan_once()
    assert engine.removed == []

    now[0] = 111.0
    discovery.scan_once()
    assert engine.removed == [(device_id, "comms_interrupted")]
    assert engine.device_map() == {}
