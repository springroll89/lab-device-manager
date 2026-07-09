from lab_device_manager.instruments.viscometer import ViscometerAdapter, HEADER, TRAILER


def _frame(viscosity, temp, shear_rate, shear_stress, torque_pct):
    visc = int(round(viscosity * 100)).to_bytes(4, "big")
    t = int(round(temp * 10)).to_bytes(2, "big")
    sr = int(round(shear_rate * 100)).to_bytes(4, "big")
    ss = int(round(shear_stress * 10)).to_bytes(4, "big")
    tq = int(round(torque_pct * 100)).to_bytes(2, "big")
    return (HEADER + b"\x20" + visc + b"\x20" + t + b"\x20" + sr
            + b"\x20" + ss + b"\x20" + tq + b"\x20" + bytes([0x00, TRAILER]))


class FakeTransport:
    def __init__(self, chunks):
        self.chunks = list(chunks)
    def read_wait(self, timeout):
        if self.chunks:
            return self.chunks.pop(0)
        return b""


def test_identity():
    assert ViscometerAdapter(FakeTransport([])).identity() == "Fangrui Viscometer"


def test_read_status_returns_latest_frame():
    # two frames arrive in two reads; adapter drains buffer and returns the LATEST (f2)
    f1 = _frame(100, 20, 5, 10, 30)
    f2 = _frame(250.5, 25.5, 12.5, 33.3, 50.0)
    tr = FakeTransport([b"\x00" + f1 + b"\xaa", f2])
    s = ViscometerAdapter(tr, read_timeout=0.5).read_status()
    assert s.state == "running"
    assert s.device_id == "Fangrui Viscometer"
    assert s.temp_c == 25.5
    assert s.metrics["viscosity_mPas"] == 250.5
    assert s.metrics["shear_rate_1s"] == 12.5
    assert s.metrics["torque_pct"] == 50.0


def test_read_status_offline_when_no_frame():
    tr = FakeTransport([b"\x00\x01\x02"])     # noise, no complete frame
    s = ViscometerAdapter(tr, read_timeout=0.05).read_status()
    assert s.state == "offline"


def test_read_status_offline_when_silent():
    s = ViscometerAdapter(FakeTransport([]), read_timeout=0.05).read_status()
    assert s.state == "offline"
