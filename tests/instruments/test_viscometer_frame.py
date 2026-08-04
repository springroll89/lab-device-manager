from lab_device_manager.instruments.viscometer import (
    checksum_sum256, parse_viscometer_frame, extract_latest_frame,
    is_valid_viscometer_frame, FRAME_LEN, HEADER, TRAILER,
)


def build_frame(viscosity, temp, shear_rate, shear_stress, torque_pct, checksum=None):
    """Build a 29-byte frame using the live-confirmed LVDV-2T scaling."""
    visc = int(round(viscosity * 100)).to_bytes(4, "big")
    temp_r = int(round(temp * 10)).to_bytes(2, "big")
    sr = int(round(shear_rate * 1000)).to_bytes(4, "big")
    ss = int(round(shear_stress * 1000 * 10)).to_bytes(4, "big")
    tq = int(round(torque_pct * 10)).to_bytes(2, "big")
    partial = (HEADER + b"\x20" + visc + b"\x20" + temp_r + b"\x20" + sr
               + b"\x20" + ss + b"\x20" + tq + b"\x20")
    checksum = checksum_sum256(partial) if checksum is None else checksum
    return partial + bytes([checksum]) + TRAILER


def test_parse_frame_decodes_all_fields():
    f = build_frame(viscosity=500.25, temp=25.5, shear_rate=12.5,
                    shear_stress=33.3, torque_pct=50.0)
    d = parse_viscometer_frame(f)
    assert d["viscosity_mPas"] == 500.25
    assert d["temperature_c"] == 25.5
    assert d["shear_rate_1s"] == 12.5
    assert d["shear_stress_Pa"] == 33.3
    assert d["shear_stress_mPa"] == 33300.0
    assert d["torque_pct"] == 50.0


def test_parse_frame_zero_values():
    d = parse_viscometer_frame(build_frame(0, 0, 0, 0, 0))
    assert d["viscosity_mPas"] == 0.0
    assert d["temperature_c"] == 0.0
    assert d["torque_pct"] == 0.0


def test_parse_real_lvdv_2t_capture():
    frame = bytes.fromhex(
        "d6 c8 18 32 20 00 00 00 00 20 00 00 20 00 00 08 7a "
        "20 00 00 00 00 20 00 00 20 2a 13 ab"
    )
    assert len(frame) == FRAME_LEN
    assert is_valid_viscometer_frame(frame)
    assert parse_viscometer_frame(frame) == {
        "viscosity_mPas": 0.0,
        "temperature_c": 0.0,
        "shear_rate_1s": 2.17,
        "shear_stress_mPa": 0.0,
        "shear_stress_Pa": 0.0,
        "torque_pct": 0.0,
    }


def test_parse_nonzero_frame_matches_instrument_screen():
    frame = bytes.fromhex(
        "d6 c8 18 32 20 00 00 00 f2 20 00 00 20 00 02 03 a0 "
        "20 00 00 0c 7a 20 00 50 20 15 13 ab"
    )
    assert parse_viscometer_frame(frame) == {
        "viscosity_mPas": 2.42,
        "temperature_c": 0.0,
        "shear_rate_1s": 132.0,
        "shear_stress_mPa": 319.4,
        "shear_stress_Pa": 0.3194,
        "torque_pct": 8.0,
    }


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


def test_extract_rejects_frame_with_wrong_separator():
    f = bytearray(build_frame(100, 20, 5, 10, 30))
    f[12] = 0x21
    frame, rest = extract_latest_frame(bytes(f))
    assert frame is None


def test_extract_rejects_frame_with_wrong_checksum():
    f = bytearray(build_frame(100, 20, 5, 10, 30))
    f[26] ^= 0xFF
    frame, rest = extract_latest_frame(bytes(f))
    assert frame is None


def test_parse_rejects_invalid_frame():
    f = bytearray(build_frame(100, 20, 5, 10, 30))
    f[-1] = 0x00
    try:
        parse_viscometer_frame(bytes(f))
    except ValueError as exc:
        assert str(exc) == "invalid Fangrui viscometer frame"
    else:
        raise AssertionError("invalid frame should be rejected")


def test_header_and_trailer_constants():
    assert HEADER == b"\xd6\xc8\x18\x32"
    assert TRAILER == b"\x13\xab"
    assert FRAME_LEN == 29
