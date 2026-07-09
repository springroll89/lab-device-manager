import pytest
from lab_device_manager.modbus_io import ModbusClient


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.written = []
    def write(self, data):
        self.written.append(data)
    def read_wait(self, timeout_s):
        if self.responses:
            return self.responses.pop(0)
        return b""
    def flush(self):
        pass


def _client(responses):
    return ModbusClient(FakeTransport(responses), slave=1,
                        gap_timeout=0.0, read_timeout=0.5, pre_silence_s=0.0)


def test_client_returns_payload_and_writes_request():
    resp = bytes.fromhex("01040a654c64616c46697500645c03")
    c = _client([resp])
    data = c.read_input_registers(1018, 5)
    assert data == bytes.fromhex("654c64616c4669750064")
    assert c.transport.written[0] == bytes.fromhex("010403fa0005107c")


def test_client_timeout_when_no_response():
    c = _client([])
    c.read_timeout = 0.05
    with pytest.raises(TimeoutError):
        c.read_input_registers(1018, 5)


def test_client_propagates_crc_error():
    c = _client([bytes([1, 4, 2, 0, 0, 0xAA, 0xBB])])
    with pytest.raises(ValueError):
        c.read_input_registers(1018, 5)
