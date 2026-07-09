from lab_device_manager.instruments.viscometer import (
    parse_viscometer_frame, extract_latest_frame, HEADER, TRAILER,
)


def build_frame(viscosity, temp, shear_rate, shear_stress, torque_pct, checksum=0x00):
    """Build a 28-byte frame. NOTE: torque scaling here matches the parser's
    guess (raw = pct*100) — this only proves self-consistency, not the device."""
    visc = int(round(viscosity * 100)).to_bytes(4, "big")
    temp_r = int(round(temp * 10)).to_bytes(2, "big")
    sr = int(round(shear_rate * 100)).to_bytes(4, "big")
    ss = int(round(shear_stress * 10)).to_bytes(4, "big")
    tq = int(round(torque_pct * 100)).to_bytes(2, "big")
    return (HEADER + b"\x20" + visc + b"\x20" + temp_r + b"\x20" + sr
            + b"\x20" + ss + b"\x20" + tq + b"\x20" + bytes([checksum, TRAILER]))


def test_parse_frame_decodes_all_fields():
    f = build_frame(viscosity=500.25, temp=25.5, shear_rate=12.5,
                    shear_stress=33.3, torque_pct=50.0)
    d = parse_viscometer_frame(f)
    assert d["viscosity_mPas"] == 500.25
    assert d["temperature_c"] == 25.5
    assert d["shear_rate_1s"] == 12.5
    assert d["shear_stress_Pa"] == 33.3
    assert d["torque_pct"] == 50.0


def test_parse_frame_zero_values():
    d = parse_viscometer_frame(build_frame(0, 0, 0, 0, 0))
    assert d["viscosity_mPas"] == 0.0
    assert d["temperature_c"] == 0.0
    assert d["torque_pct"] == 0.0


def test_extract_latest_returns_last_complete_frame():
    f1 = build_frame(100, 20, 5, 10, 30)
    f2 = build_frame(200, 25, 6, 12, 40)
    buf = b"\x00\x00" + f1 + b"\xaa" + f2 + b"\xbb"
    frame, rest = extract_latest_frame(buf)
    assert frame == f2
    assert rest == b"\xbb"


def test_extract_returns_none_when_no_complete_frame():
    buf = b"\x00" + HEADER + b"\x20"  # header but not enough bytes
    frame, rest = extract_latest_frame(buf)
    assert frame is None


def test_extract_rejects_frame_with_wrong_trailer():
    f = bytearray(build_frame(100, 20, 5, 10, 30))
    f[-1] = 0x00                       # corrupt trailer
    frame, rest = extract_latest_frame(bytes(f))
    assert frame is None


def test_header_and_trailer_constants():
    assert HEADER == b"\xd6\xc8\x18\x32"
    assert TRAILER == 0x40
