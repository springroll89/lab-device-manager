import json
import subprocess
from pathlib import Path


def test_outbox_is_account_scoped_and_keeps_retryable_failures():
    module = (
        Path(__file__).parents[2]
        / "lab_device_manager/web/static/outbox.js"
    )
    script = f"""
      const outbox = require({json.dumps(str(module))});
      console.log(JSON.stringify({{
        a: outbox.storageKey(11),
        b: outbox.storageKey(12),
        server: outbox.classifyHttpStatus(503),
        limited: outbox.classifyHttpStatus(429),
        conflict: outbox.classifyHttpStatus(409),
        invalid: outbox.classifyHttpStatus(400)
      }}));
    """
    data = json.loads(
        subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )

    assert data["a"] != data["b"]
    assert data["server"] == "retry"
    assert data["limited"] == "retry"
    assert data["conflict"] == "conflict"
    assert data["invalid"] == "discard"
