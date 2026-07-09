import pytest
from lab_device_manager.instruments.factory import make_adapter
from lab_device_manager.instruments.leadfluid_tyd02 import TYD02Adapter
from lab_device_manager.instruments.stirrer import StirrerAdapter
from lab_device_manager.instruments.viscometer import ViscometerAdapter


class FakeTransport:
    pass


def test_factory_pump():
    a = make_adapter("tyd02", FakeTransport(), wordorder="CDAB")
    assert isinstance(a, TYD02Adapter)


def test_factory_stirrer():
    a = make_adapter("stirrer", FakeTransport())
    assert isinstance(a, StirrerAdapter)


def test_factory_viscometer():
    a = make_adapter("viscometer", FakeTransport())
    assert isinstance(a, ViscometerAdapter)


def test_factory_case_insensitive_and_default():
    assert isinstance(make_adapter("STIRRER", FakeTransport()), StirrerAdapter)
    assert isinstance(make_adapter("", FakeTransport()), TYD02Adapter)


def test_factory_unknown_raises():
    with pytest.raises(ValueError):
        make_adapter("densitometer", FakeTransport())
