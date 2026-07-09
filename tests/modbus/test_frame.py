import pytest
from lab_device_manager.modbus_io import (
    crc16, append_crc, build_read_request, parse_response, ModbusError,
    FC_READ_INPUT,
)

# Real captured frame: read company-info @1018, count 5, slave 1.
def test_crc16_known_vector():
    assert crc16(bytes.fromhex("010403fa0005")) == 0x7C10

def test_append_crc_low_byte_first():
    assert append_crc(bytes.fromhex("010403fa0005")) == bytes.fromhex("010403fa0005107c")

def test_build_read_request_matches_capture():
    req = build_read_request(1, FC_READ_INPUT, 1018, 5)
    assert req == bytes.fromhex("010403fa0005107c")

def test_parse_response_returns_data_bytes():
    # captured reply: slave=1 fc=04 bytecount=0a + 10 data bytes + CRC 5c03
    frame = bytes.fromhex("01040a654c64616c46697500645c03")
    assert parse_response(frame, 1, FC_READ_INPUT) == bytes.fromhex("654c64616c4669750064")

def test_parse_response_raises_on_bad_crc():
    bad = bytes([1, 4, 2, 0, 0, 0xAA, 0xBB])
    with pytest.raises(ValueError):
        parse_response(bad, 1, FC_READ_INPUT)

def test_parse_response_raises_modbus_exception():
    exc = append_crc(bytes([1, 0x84, 0x02]))  # exception: fc|0x80, code=2
    with pytest.raises(ModbusError) as ei:
        parse_response(exc, 1, FC_READ_INPUT)
    assert ei.value.code == 0x02
