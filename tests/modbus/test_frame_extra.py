import pytest
from lab_device_manager.modbus_io import (
    SerialTransport,
    append_crc,
    parse_response,
    FC_READ_INPUT,
)


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


def test_parse_response_rejects_declared_byte_count_mismatch():
    frame = append_crc(bytes([1, FC_READ_INPUT, 4, 0, 1]))
    with pytest.raises(ValueError, match="byte count mismatch"):
        parse_response(frame, 1, FC_READ_INPUT)


def test_parse_response_rejects_requested_register_count_mismatch():
    frame = append_crc(bytes([1, FC_READ_INPUT, 2, 0, 1]))
    with pytest.raises(ValueError, match="response length mismatch"):
        parse_response(frame, 1, FC_READ_INPUT, expected_data_bytes=4)


def test_serial_open_closes_handle_when_buffer_reset_fails(monkeypatch):
    class BrokenSerial:
        closed = False

        def reset_input_buffer(self):
            raise OSError("reset failed")

        def reset_output_buffer(self):
            raise AssertionError("must not continue after input reset")

        def close(self):
            self.closed = True

    opened = BrokenSerial()
    monkeypatch.setattr(
        "lab_device_manager.modbus_io.serial.Serial",
        lambda **_kwargs: opened,
    )
    transport = SerialTransport("/dev/fake")
    with pytest.raises(OSError, match="reset failed"):
        transport.open()
    assert opened.closed is True
    assert transport._ser is None
