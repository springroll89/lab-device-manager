import struct
import pytest
from lab_device_manager.instruments.whd46_33 import WHD46Adapter, _raw_to_value


def u16(v):
    return struct.pack(">H", v)


class FakeClient:
    def __init__(self, hold):
        self.hold = hold

    def read_holding_registers(self, reg, count):
        return self.hold[(reg, count)]


def _temp_humid_bytes(temp: float, humid: float) -> bytes:
    t_raw = int(temp * 10)
    if t_raw < 0:
        t_raw += 0x10000
    h_raw = int(humid * 10)
    if h_raw < 0:
        h_raw += 0x10000
    return u16(t_raw) + u16(h_raw)


def _adapter(ch1_temp=25.0, ch1_humid=50.0, ch2_temp=26.0, ch2_humid=45.0, ch3_temp=27.0, ch3_humid=40.0):
    hold = {
        (1, 6): (_temp_humid_bytes(ch1_temp, ch1_humid) +
                 _temp_humid_bytes(ch2_temp, ch2_humid) +
                 _temp_humid_bytes(ch3_temp, ch3_humid)),
    }
    return WHD46Adapter(FakeClient(hold), slave=1)


def test_raw_to_value():
    assert _raw_to_value(250) == 25.0
    assert _raw_to_value(500) == 50.0
    assert _raw_to_value(0xFFE6) == -2.6


def test_identity():
    a = _adapter()
    assert "WHD46-33" in a.identity()


def test_read_status_decodes_channels():
    s = _adapter(ch1_temp=23.5, ch1_humid=45.2, ch2_temp=24.1, ch2_humid=43.8, ch3_temp=22.9, ch3_humid=46.5).read_status()
    assert s.device_id is not None
    assert s.state == "running"
    assert s.work_mode == "monitoring"
    assert s.temp_c == pytest.approx(23.5, abs=0.1)
    assert s.metrics["ch1_temp_c"] == 23.5
    assert s.metrics["ch1_humid_rh"] == 45.2
    assert s.metrics["ch2_temp_c"] == 24.1
    assert s.metrics["ch2_humid_rh"] == 43.8
    assert s.metrics["ch3_temp_c"] == 22.9
    assert s.metrics["ch3_humid_rh"] == 46.5
    assert s.metrics["avg_humid_rh"] == pytest.approx(45.166, abs=0.01)


def test_read_status_includes_channels_list():
    s = _adapter().read_status()
    channels = s.metrics.get("channels", [])
    assert len(channels) == 3
    assert channels[0]["temp"] == 25.0
    assert channels[0]["humid"] == 50.0
    assert channels[1]["temp"] == 26.0
    assert channels[1]["humid"] == 45.0
    assert channels[2]["temp"] == 27.0
    assert channels[2]["humid"] == 40.0


def test_temp_c_is_average():
    s = _adapter(ch1_temp=20.0, ch2_temp=25.0, ch3_temp=30.0).read_status()
    assert s.temp_c == 25.0
