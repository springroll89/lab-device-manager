import time

from lab_device_manager.instruments.base import StatusSnapshot, offline_snapshot
from lab_device_manager.runtime.communication import communication_health


def test_fresh_valid_frame_is_communicating():
    snap = StatusSnapshot(
        timestamp=time.time(),
        state="stopped",
        work_mode="",
        device_id="stirrer",
    )

    health = communication_health(snap)

    assert health["code"] == "communicating"
    assert health["label"] == "通讯正常"


def test_modbus_timeout_means_channel_connected_but_instrument_unresponsive():
    snap = offline_snapshot(
        "stirrer",
        "no Modbus response within read_timeout",
        communication_status="instrument_unresponsive",
    )

    health = communication_health(snap)

    assert health["code"] == "instrument_unresponsive"
    assert health["label"] == "仪器无响应"


def test_gateway_connection_failure_is_reported_separately():
    snap = offline_snapshot(
        "stirrer",
        "cannot connect to 192.168.1.125:4003",
        communication_status="gateway_offline",
    )

    health = communication_health(snap)

    assert health["code"] == "gateway_offline"
    assert health["label"] == "网关离线"


def test_old_valid_frame_is_data_interrupted():
    snap = StatusSnapshot(
        timestamp=time.time() - 16,
        state="running",
        work_mode="",
        device_id="pump",
    )

    health = communication_health(snap)

    assert health["code"] == "data_interrupted"
    assert health["label"] == "数据中断"
