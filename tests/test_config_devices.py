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


def test_loads_tcp_device_channel(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(textwrap.dedent("""
        [[devices]]
        name = "pump-1"
        type = "tyd02"
        transport = "tcp"
        host = "192.168.1.125"
        tcp_port = 4002
    """), encoding="utf-8")

    cfg = load_config(str(p))

    assert cfg.devices[0].transport == "tcp"
    assert cfg.devices[0].host == "192.168.1.125"
    assert cfg.devices[0].tcp_port == 4002


def test_rejects_tcp_device_without_endpoint(tmp_path):
    import pytest

    p = tmp_path / "c.toml"
    p.write_text(textwrap.dedent("""
        [[devices]]
        name = "pump-1"
        type = "tyd02"
        transport = "tcp"
    """), encoding="utf-8")

    with pytest.raises(ValueError, match="requires host and tcp_port"):
        load_config(str(p))

def test_defaults(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('db_path = "./x.db"\n', encoding="utf-8")  # no devices key -> no devices
    cfg = load_config(str(p))
    assert cfg.web_port == 7800 and cfg.sample_interval_ms == 1000 and cfg.devices == ()
    assert cfg.web_host == "0.0.0.0"
    assert cfg.public_base_url == ""
    assert cfg.tls_certfile == ""
    assert cfg.tls_keyfile == ""


def test_rejects_nonpositive_sample_interval(tmp_path):
    import pytest

    p = tmp_path / "c.toml"
    p.write_text("sample_interval_ms = 0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sample_interval_ms"):
        load_config(str(p))


def test_loads_lan_and_https_settings(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(textwrap.dedent("""
        web_host = "0.0.0.0"
        web_port = 8443
        public_base_url = "https://lab.puricore.example"
        tls_certfile = "/etc/puricore/fullchain.pem"
        tls_keyfile = "/etc/puricore/privkey.pem"
    """), encoding="utf-8")

    cfg = load_config(str(p))

    assert cfg.web_host == "0.0.0.0"
    assert cfg.web_port == 8443
    assert cfg.public_base_url == "https://lab.puricore.example"
    assert cfg.tls_certfile.endswith("fullchain.pem")
    assert cfg.tls_keyfile.endswith("privkey.pem")


def test_rejects_incomplete_tls_pair(tmp_path):
    import pytest
    p = tmp_path / "c.toml"
    p.write_text(
        'tls_certfile = "/tmp/fullchain.pem"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="tls_certfile.*tls_keyfile"):
        load_config(str(p))


def test_rejects_non_http_public_base_url(tmp_path):
    import pytest
    p = tmp_path / "c.toml"
    p.write_text(
        'public_base_url = "javascript:alert(1)"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="public_base_url"):
        load_config(str(p))


def test_rejects_devices_dict(tmp_path):
    import pytest
    p = tmp_path / "c.toml"
    p.write_text('[devices]\n', encoding="utf-8")  # [devices] (dict) instead of [[devices]]
    with pytest.raises(ValueError, match="\\[\\[devices\\]\\]"):
        load_config(str(p))


def test_default_load_prefers_untracked_local_config(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        'web_port = 7800\nsecret_key = "test"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.local.toml").write_text(
        'web_port = 7900\nsecret_key = "test"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert load_config().web_port == 7900
