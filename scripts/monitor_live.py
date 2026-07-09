#!/usr/bin/env python3
"""Live end-to-end validation of Slice A.1: pump -> SQLite -> run/event detection.
Run with the pump connected:  python scripts/monitor_live.py [seconds]
Then start/stop a dispense on the pump panel; runs + events (incl. stall) are
auto-recorded into ./data/lab_device_manager.db. Press Ctrl-C to stop early."""
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lab_device_manager.config import load_config
from lab_device_manager.db.repository import Repository
from lab_device_manager.runtime.engine import Engine, serial_adapter_factory

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0


def main():
    os.makedirs("./data", exist_ok=True)
    cfg = load_config()
    db_path = cfg.db_path
    repo = Repository(db_path)
    devs = cfg.devices
    eng = Engine(repo, list(devs), sample_interval_s=1.0,
                 adapter_factory=serial_adapter_factory,
                 on_event=lambda e: print(f"  EVENT {e['event_type']} ({e['severity']}) run={e['run_id']}"))
    print(f"monitoring pump for {DURATION}s -> {db_path}")
    print("(start/stop a dispense on the pump panel; runs auto-record)")
    eng.start()
    try:
        time.sleep(DURATION)
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        runs = repo.list_untagged_runs()
        evs = repo.list_recent_events(limit=20)
        print(f"\nrecorded {len(runs)} untagged run(s); last {len(evs)} event(s):")
        for e in evs:
            print(f"  {e.ts}  {e.event_type} ({e.severity})")
        eng.stop()
        repo.close()


if __name__ == "__main__":
    main()
