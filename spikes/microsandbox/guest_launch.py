"""Launch the persistent guest workload outside msb managed-job tracking."""

import subprocess
from pathlib import Path

with Path("/tmp/guest-state.log").open("wb") as log:
    process = subprocess.Popen(
        ["python", "/tmp/guest_state.py"],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
Path("/tmp/guest-state.pid").write_text(str(process.pid), encoding="ascii")
