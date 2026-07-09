from __future__ import annotations
import threading
import webbrowser

from lab_device_manager.config import load_config
from lab_device_manager.db.repository import Repository
from lab_device_manager.runtime.engine import Engine, serial_adapter_factory
from lab_device_manager.web.app import create_app


def main():  # pragma: no cover - wiring + server lifecycle
    cfg = load_config()
    repo = Repository(cfg.db_path)
    eng = Engine(repo, list(cfg.devices), cfg.sample_interval_ms / 1000.0,
                 adapter_factory=serial_adapter_factory)
    eng.start()
    app = create_app(eng, repo, secret_key=cfg.secret_key, login_password=cfg.login_password)
    try:
        if cfg.auto_open_browser:
            url = f"http://127.0.0.1:{cfg.web_port}/"
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        app.run(host="127.0.0.1", port=cfg.web_port, threaded=True, use_reloader=False)
    finally:
        eng.stop()
        repo.close()


if __name__ == "__main__":
    main()
