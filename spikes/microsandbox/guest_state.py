"""Guest workload for the microsandbox full-snapshot spike.

The process keeps ``value`` only in memory. It waits for a trigger file,
then increments the in-memory value and writes a result. A successful full
snapshot restore must therefore produce value=42 without rerunning setup.
"""

import json
import time
from pathlib import Path

STARTED = Path("/tmp/guest-state-started.json")
RESUME = Path("/tmp/guest-state-resume")
RESULT = Path("/tmp/guest-state-result.json")
DISK_PAYLOAD = Path("/tmp/guest-disk-payload.txt")


def main() -> None:
    value = 41
    message = "resident-in-python-memory"
    DISK_PAYLOAD.write_text("disk-before-snapshot", encoding="utf-8")
    STARTED.write_text(json.dumps({"value": value, "message": message}), encoding="utf-8")

    while not RESUME.exists():
        time.sleep(0.05)

    value += 1
    RESULT.write_text(
        json.dumps(
            {
                "value": value,
                "message": message,
                "disk": DISK_PAYLOAD.read_text(encoding="utf-8"),
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
