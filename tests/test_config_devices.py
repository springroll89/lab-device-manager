import textwrap
from lab_device_manager.config import load_config

def test_loads_devices(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(textwrap.dedent("""
        db_path = "./x.db"
        [[devices]]
        name = "pump-1"
        type = "tyd02"
        serial_port = "/dev/x"
    """), encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.db_path == "./x.db"
    assert len(cfg.devices) == 1
    assert cfg.devices[0].name == "pump-1"
    assert cfg.devices[0].serial_port == "/dev/x"

def test_defaults(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('db_path = "./x.db"\n', encoding="utf-8")  # no devices key -> no devices
    cfg = load_config(str(p))
    assert cfg.web_port == 7800 and cfg.sample_interval_ms == 1000 and cfg.devices == ()


def test_rejects_devices_dict(tmp_path):
    import pytest
    p = tmp_path / "c.toml"
    p.write_text('[devices]\n', encoding="utf-8")  # [devices] (dict) instead of [[devices]]
    with pytest.raises(ValueError, match="\\[\\[devices\\]\\]"):
        load_config(str(p))
