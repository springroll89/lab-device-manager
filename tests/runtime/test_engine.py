import time
import lab_device_manager.runtime.engine as engine_module
from lab_device_manager.runtime.engine import Engine, device_adapter_factory
from lab_device_manager.runtime.types import DeviceConfig
from lab_device_manager.db.repository import Repository
from lab_device_manager.instruments.base import StatusSnapshot


def _snap(state):
    return StatusSnapshot(timestamp=time.time(), state=state, work_mode="仅注入",
                          device_id="d", acc_volume=1.0, acc_unit="mL")


class ScriptedAdapter:
    def __init__(self, seq):
        self.seq = seq
        self.i = 0
    def identity(self):
        return "d"
    def read_status(self):
        s = self.seq[min(self.i, len(self.seq) - 1)]
        self.i += 1
        return s


def test_engine_records_run_to_db():
    repo = Repository(":memory:")
    seq = [_snap("stopped"), _snap("running"), _snap("running"), _snap("stopped")]
    devices = [DeviceConfig(name="pump-1", type="tyd02")]
    def factory(dc):
        return ScriptedAdapter(seq), (lambda: None)
    eng = Engine(repo, devices, sample_interval_s=0.02, adapter_factory=factory)
    eng.start()
    time.sleep(0.25)
    eng.stop()
    runs = repo.list_untagged_runs()
    assert len(runs) == 1
    assert runs[0].end_status == "completed"
    assert 1 in eng.latest()                     # device_id 1 present in latest-store


def test_tolerates_unreachable_device():
    repo = Repository(":memory:")
    seq = [_snap("stopped"), _snap("running"), _snap("stopped")]
    devices = [DeviceConfig(name="dead", type="tyd02"),
               DeviceConfig(name="live", type="tyd02")]
    def factory(dc):
        if dc.name == "dead":
            raise OSError("no such port")
        return ScriptedAdapter(seq), (lambda: None)
    eng = Engine(repo, devices, sample_interval_s=0.02, adapter_factory=factory)
    eng.start()                                   # must NOT raise on the dead device
    time.sleep(0.25)
    eng.stop()
    latest = eng.latest()
    assert len(latest) == 2                       # both registered
    assert any(s.state == "offline" for s in latest.values())   # dead device offline
    assert len(repo.list_untagged_runs()) == 1    # live produced a run; dead produced none


def test_get_adapter_returns_adapter_for_requested_device():
    repo = Repository(":memory:")
    devices = [
        DeviceConfig(name="pump-1", type="tyd02"),
        DeviceConfig(name="pump-2", type="tyd02"),
    ]
    adapters = {}

    def factory(dc):
        adapter = ScriptedAdapter([_snap("stopped")])
        adapters[dc.name] = adapter
        return adapter, (lambda: None)

    eng = Engine(repo, devices, sample_interval_s=0.02, adapter_factory=factory)
    eng.start()
    try:
        device_ids = {
            config.name: device_id
            for device_id, config in eng.device_map().items()
        }
        assert eng._get_adapter(device_ids["pump-1"]) is adapters["pump-1"]
        assert eng._get_adapter(device_ids["pump-2"]) is adapters["pump-2"]
    finally:
        eng.stop()


def test_reconnect_and_disconnect_keep_connection_owned_by_engine():
    repo = Repository(":memory:")
    devices = [
        DeviceConfig(
            name="whd-1",
            type="whd46",
            serial_port="/dev/old",
        )
    ]
    opened_ports = []
    closed_ports = []

    def factory(config):
        opened_ports.append(config.serial_port)
        adapter = ScriptedAdapter([_snap("running")])
        return adapter, lambda: closed_ports.append(config.serial_port)

    engine = Engine(
        repo,
        devices,
        sample_interval_s=0.02,
        adapter_factory=factory,
    )
    engine.start()
    device_id = next(iter(engine.device_map()))
    try:
        result = engine.reconnect_device(device_id, "/dev/new")
        assert result == {"ok": True, "port": "/dev/new"}
        assert opened_ports == ["/dev/old", "/dev/new"]
        assert closed_ports == ["/dev/old"]
        assert engine.device_map()[device_id].serial_port == "/dev/new"

        disconnected = engine.disconnect_device(device_id)
        assert disconnected == {"ok": True}
        assert closed_ports == ["/dev/old", "/dev/new"]
        assert engine.latest()[device_id].state == "offline"
        assert engine._get_adapter(device_id) is None
    finally:
        engine.stop()


def test_engine_start_closes_run_left_open_by_previous_process():
    repo = Repository(":memory:")
    device_id = repo.upsert_device("pump-stale", "tyd02")
    run_id = repo.open_run(device_id, 1, 1000, {})
    engine = Engine(repo, [], sample_interval_s=0.02, adapter_factory=lambda _: None)

    engine.start()

    stale = repo.get_run(run_id)
    assert stale.ended_ms is not None
    assert stale.end_status == "interrupted_restart"


def test_device_adapter_factory_uses_configured_tcp_channel(monkeypatch):
    events = []

    class FakeTcpTransport:
        def __init__(self, host, port, connect_timeout):
            events.append(("create", host, port, connect_timeout))

        def open(self):
            events.append(("open",))

        def close(self):
            events.append(("close",))

    adapter = object()
    monkeypatch.setattr(engine_module, "TcpTransport", FakeTcpTransport)
    monkeypatch.setattr(
        engine_module,
        "make_adapter",
        lambda name, transport, **_kwargs: adapter,
    )
    config = DeviceConfig(
        name="pump-1",
        type="tyd02",
        transport="tcp",
        host="192.168.1.125",
        tcp_port=4002,
        connect_timeout_s=0.3,
    )

    built, cleanup = device_adapter_factory(config)

    assert built is adapter
    assert events == [("create", "192.168.1.125", 4002, 0.3), ("open",)]
    cleanup()
    assert events[-1] == ("close",)
