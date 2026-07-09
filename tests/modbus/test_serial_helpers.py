import pytest
from lab_device_manager.modbus_io import _parity_code


def test_parity_code_even():
    assert _parity_code("EVEN") == "E"
    assert _parity_code("even") == "E"


def test_parity_code_odd():
    assert _parity_code("ODD") == "O"
    assert _parity_code("odd") == "O"


def test_parity_code_none():
    assert _parity_code("NONE") == "N"
    assert _parity_code("none") == "N"


def test_parity_code_invalid():
    with pytest.raises(ValueError):
        _parity_code("MARK")