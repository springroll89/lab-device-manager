import socket

import pytest

from lab_device_manager.modbus_io import (
    GatewayUnavailableError,
    TcpTransport,
)


class FakeSocket:
    def __init__(self, reads=()):
        self.reads = list(reads)
        self.sent = []
        self.timeout = None
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, _size):
        if not self.reads:
            raise socket.timeout
        value = self.reads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed = True


def test_tcp_transport_connects_lazily_and_carries_raw_serial_bytes(
    monkeypatch,
):
    sock = FakeSocket([b"\x01\x04\x02\x00\x01"])
    opened = []

    def create_connection(address, timeout):
        opened.append((address, timeout))
        return sock

    monkeypatch.setattr(socket, "create_connection", create_connection)
    transport = TcpTransport("192.168.1.125", 4002, connect_timeout=0.2)

    transport.open()
    assert opened == []

    transport.write(b"\x01\x04")

    assert opened == [(("192.168.1.125", 4002), 0.2)]
    assert sock.sent == [b"\x01\x04"]
    assert transport.read_wait(0.1) == b"\x01\x04\x02\x00\x01"


def test_tcp_transport_retries_on_the_next_poll_after_gateway_returns(
    monkeypatch,
):
    sock = FakeSocket()
    attempts = iter([OSError("network unreachable"), sock])

    def create_connection(_address, _timeout):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(socket, "create_connection", create_connection)
    transport = TcpTransport("192.168.1.125", 4003)
    transport.open()

    with pytest.raises(GatewayUnavailableError):
        transport.write(b"first poll")

    transport.write(b"next poll")
    assert sock.sent == [b"next poll"]


def test_tcp_transport_treats_closed_channel_as_gateway_unavailable(
    monkeypatch,
):
    sock = FakeSocket([b""])
    monkeypatch.setattr(socket, "create_connection", lambda *_args: sock)
    transport = TcpTransport("192.168.1.125", 4001)
    transport.open()

    with pytest.raises(GatewayUnavailableError):
        transport.read_wait(0.1)

    assert sock.closed is True
