from lab_device_manager.config import load_config
from lab_device_manager.db.repository import Repository
from lab_device_manager.runtime.engine import Engine, serial_adapter_factory

cfg = load_config()
repo = Repository(cfg.db_path)
eng = Engine(repo, list(cfg.devices), cfg.sample_interval_ms / 1000.0,
             adapter_factory=serial_adapter_factory)
eng.start()

print(f"_latest keys: {list(eng._latest.keys())}")
print(f"_device_map keys: {list(eng._device_map.keys())}")

for did, snap in eng._latest.items():
    dc = eng._device_map.get(did)
    print(f"ID:{did} Name:{dc.name if dc else 'N/A'} State:{snap.state}")