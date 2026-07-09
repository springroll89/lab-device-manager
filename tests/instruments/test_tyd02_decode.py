import struct
import pytest
from lab_device_manager.instruments.leadfluid_tyd02 import (
    decode_ascii, decode_float, decode_uint32, decode_uint16, decode_int16,
    VOLUME_UNITS, WORK_MODES,
)


def test_decode_ascii_leadfluid_capture():
    # real captured company-info data bytes
    assert decode_ascii(bytes.fromhex("654c64616c4669750064")) == "LeadFluid"


def test_decode_ascii_odd_length():
    # 'A' alone -> stored as (00, 0x41); 2 bytes
    assert decode_ascii(bytes([0x00, 0x41])) == "A"


def test_decode_float_cdab_roundtrip():
    v = 12.5
    b = struct.pack(">f", v)
    cdab = bytes([b[2], b[3], b[0], b[1]])
    assert decode_float(cdab, "CDAB") == pytest.approx(v)


def test_decode_float_abcd_roundtrip():
    v = 0.001
    assert decode_float(struct.pack(">f", v), "ABCD") == pytest.approx(v)


def test_decode_uint32_both_orders():
    abcd = bytes([0x01, 0x02, 0x03, 0x04])
    cdab = bytes([0x03, 0x04, 0x01, 0x02])
    assert decode_uint32(abcd, "ABCD") == 0x01020304
    assert decode_uint32(cdab, "CDAB") == 0x01020304


def test_decode_uint16_and_int16():
    assert decode_uint16(bytes([0x00, 0x2A])) == 42
    assert decode_int16(bytes([0xFF, 0xD8])) == -40


def test_maps():
    assert VOLUME_UNITS[2] == "mL"
    assert WORK_MODES[4] == "连续"
