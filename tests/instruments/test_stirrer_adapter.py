import struct
from lab_device_manager.instruments.stirrer import (
    StirrerAdapter, decode_alarm_flags, derive_state,
)


def u16(v):
    return struct.pack(">H", v)


def block(values):
    """List of 16 register ints (regs 0..15) -> 32 bytes big-endian."""
    return b"".join(u16(v) for v in values)


class FakeClient:
    def __init__(self, holding):
        self.holding = holding          # {(reg, count): bytes}
    def read_holding_registers(self, reg, count):
        return self.holding[(reg, count)]


# regs 0..15: type, display_mode, temp_raw, set_temp_raw, speed, set_speed,
#   temp_min, temp_max, speed_min, speed_max, alarm, heater, stirrer, mode, pwr_loss, sensor
def test_read_status_decodes_fields():
    regs = [203, 1, 255, 300, 300, 400, 0, 320, 100, 1500,
            0, 1, 0, 2, 0, 1]
    s = StirrerAdapter(FakeClient({(0, 16): block(regs)})).read_status()
    assert s.device_id == "Stirrer type 203"
    assert s.state == "running"          # heater on, no alarm
    assert s.work_mode == "模式2"
    assert s.temp_c == 25.5              # 255 / 10 (display_mode=1)
    assert s.alarm is False
    assert s.error_code == 0
    assert s.metrics["set_temp"] == 30.0
    assert s.metrics["speed"] == 300
    assert s.metrics["set_speed"] == 400
    assert s.metrics["heater_on"] is True
    assert s.metrics["stirrer_on"] is False
    assert s.metrics["mode"] == 2
    assert s.metrics["sensor_connected"] is True
    assert s.metrics["temp_max"] == 320
    assert s.metrics["alarm_flags"] == []


def test_alarm_state_and_flags():
    # alarm bit 4 (电机堵转) set; heater on but alarm wins
    regs = [203, 0, 25, 30, 0, 0, 0, 320, 100, 1500,
            (1 << 4), 1, 0, 1, 0, 0]
    s = StirrerAdapter(FakeClient({(0, 16): block(regs)})).read_status()
    assert s.state == "alarm"
    assert s.alarm is True
    assert s.error_code == 16
    assert s.metrics["alarm_flags"] == ["电机堵转"]


def test_stopped_state():
    regs = [203, 0, 25, 30, 0, 0, 0, 320, 100, 1500, 0, 0, 0, 1, 0, 0]
    s = StirrerAdapter(FakeClient({(0, 16): block(regs)})).read_status()
    assert s.state == "stopped"


def test_display_mode_no_decimal():
    regs = [203, 0, 25, 30, 0, 0, 0, 320, 100, 1500, 0, 0, 0, 1, 0, 0]
    s = StirrerAdapter(FakeClient({(0, 16): block(regs)})).read_status()
    assert s.temp_c == 25.0              # display_mode 0 -> not divided
    assert s.metrics["set_temp"] == 30.0


def test_identity_reads_machine_type():
    a = StirrerAdapter(FakeClient({(0, 1): u16(203)}))
    assert a.identity() == "Stirrer type 203"


def test_decode_alarm_flags_multiple():
    bits = (1 << 0) | (1 << 4) | (1 << 10)
    assert decode_alarm_flags(bits) == ["内部传感器故障", "电机堵转", "传感器保护报警4"]


def test_derive_state_priority():
    assert derive_state(0, 1, 1) == "running"
    assert derive_state(0, 0, 1) == "running"
    assert derive_state(1 << 4, 1, 1) == "alarm"    # alarm beats running
    assert derive_state(0, 0, 0) == "stopped"
