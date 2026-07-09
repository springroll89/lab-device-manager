from lab_device_manager.config import load_config

cfg = load_config()
print(f"Total devices in config: {len(cfg.devices)}")
for i, dc in enumerate(cfg.devices):
    print(f"{i+1}. Name:{dc.name} Type:{dc.type} Alias:{dc.alias}")