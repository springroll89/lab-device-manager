from __future__ import annotations
import threading
import webbrowser

from lab_device_manager.config import load_config
from lab_device_manager.db.repository import Repository
from lab_device_manager.runtime.engine import Engine, device_adapter_factory
from lab_device_manager.web.app import create_app


def main():  # pragma: no cover - wiring + server lifecycle
    cfg = load_config()
    repo = Repository(cfg.db_path)
    eng = Engine(repo, list(cfg.devices), cfg.sample_interval_ms / 1000.0,
                 adapter_factory=device_adapter_factory)
    eng.start()
    app = create_app(
        eng,
        repo,
        secret_key=cfg.secret_key,
        public_base_url=cfg.public_base_url,
        topology_collector_name=cfg.topology_collector_name,
        topology_switch_name=cfg.topology_switch_name,
        topology_switch_model=cfg.topology_switch_model,
    )
    try:
        if cfg.auto_open_browser:
            scheme = "https" if cfg.tls_certfile else "http"
            url = (
                f"{cfg.public_base_url}/"
                if cfg.public_base_url
                else f"{scheme}://127.0.0.1:{cfg.web_port}/"
            )
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        ssl_context = (
            (cfg.tls_certfile, cfg.tls_keyfile)
            if cfg.tls_certfile and cfg.tls_keyfile
            else None
        )
        app.run(
            host=cfg.web_host,
            port=cfg.web_port,
            threaded=True,
            use_reloader=False,
            ssl_context=ssl_context,
        )
    finally:
        eng.stop()
        repo.close()


if __name__ == "__main__":
    main()
