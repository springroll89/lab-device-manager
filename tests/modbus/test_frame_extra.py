import pytest
from lab_device_manager.modbus_io import append_crc, parse_response, FC_READ_INPUT


def test_parse_response_slave_mismatch():
    # CRC-valid frame addressed to a different slave
    frame = append_crc(bytes([2, 0x04, 0x02, 0x00, 0x00]))   # slave = 2
    with pytest.raises(ValueError):
        parse_response(frame, 1, FC_READ_INPUT)              # expected slave = 1


def test_parse_response_fc_mismatch():
    # valid frame but for a different function code (not an exception frame)
    frame = append_crc(bytes([1, 0x03, 0x02, 0x00, 0x00]))   # fc = 0x03
    with pytest.raises(ValueError):
        parse_response(frame, 1, FC_READ_INPUT)              # expected fc = 0x04
