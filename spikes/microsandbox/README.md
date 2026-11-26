# Microsandbox full-snapshot spike

Throwaway validation for the MCP gateway code-mode concept. This is not
production code.

## What the spike checks

- a microVM starts with the `python:3.12` OCI image and 512 MiB RAM;
- `--no-net` blocks an attempted HTTPS request to PyPI;
- a Python process holds a variable only in memory while waiting for a trigger;
- a full snapshot captures disk, memory and execution state;
- the source VM is removed before restore, so two clones never run together;
- after restore, the same Python process resumes and changes `41` to `42`;
- disk contents from before the snapshot are available;
- a second, sequential restore discards a post-snapshot cancellation marker;
- optional `--cow-memory` validates CoW memory on the rollback restore.

The host runner uses only the Python standard library. It removes created
sandboxes and snapshot groups unless `--keep` is passed.

## Windows result

Environment: Windows 11 x64, WHP active, `msb 0.8.2`, host Python 3.14.6,
guest Python 3.12.16. Commands:

```powershell
uv tool install microsandbox
.\spikes\microsandbox\run_windows.ps1
.\spikes\microsandbox\run_windows.ps1 -CowMemory
```

Measured results from the final runs:

| Check | Normal restore | CoW rollback variant |
| --- | ---: | ---: |
| Cached sandbox creation | 855.1 ms | 822.7 ms |
| Full snapshot | 618.0 ms | 632.2 ms |
| Warm restore after source removal | 857.7 ms | 794.4 ms |
| Sequential rollback restore | 832.3 ms | 945.4 ms |
| Python memory and process continuation | passed | passed |
| Disk restore and cancellation rollback | passed | passed |
| Network disabled | passed | passed |

Raw reports: `results-windows.json` and `results-windows-cow.json`.
These timings are single-run observations on one Windows laptop, not an SLO
or a benchmark of the Linux production target.

## Linux package

Prerequisites: Linux x86-64 with KVM available and accessible at `/dev/kvm`,
`python3`, and `uv` for the pinned installation. The installer installs
`microsandbox==0.8.2` only when `msb` is absent.

```sh
chmod +x spikes/microsandbox/install_linux.sh spikes/microsandbox/run_linux.sh
./spikes/microsandbox/run_linux.sh

# Optional CoW rollback run:
COW_MEMORY=1 ./spikes/microsandbox/run_linux.sh

# Optional image and output overrides:
MSB_IMAGE=python:3.14 ./spikes/microsandbox/run_linux.sh /tmp/msb-results.json
```

`MICROSANDBOX_VERSION` overrides the installation version when installation
is needed. The script does not change an already installed `msb` version.

The user also reported a successful run of `run_linux.sh` on Linux. No Linux
result report was supplied, so Linux-specific timings are not listed here.

## Implementation findings

- A persistent workload started with `msb exec --detach` is a managed job.
  Microsandbox rejects full capture while managed jobs are active. The spike
  therefore launches its persistent interpreter outside managed-job tracking
  with `guest_launch.py`. Production should start the notebook kernel as a VM
  workload and explicitly test the chosen lifecycle rather than assume that a
  detached `exec` job can be snapshotted.
- On Windows, a detached VM can inherit CLI output pipes and make a host
  `subprocess.run(..., capture_output=True)` wait for VM termination. The host
  runner uses file output for VM-launching commands and pipes for ordinary
  commands.
- Some `msb` errors were observed with process exit code 0. The runner also
  checks error text and verifies guest state instead of relying only on exit
  codes.

## Not validated yet

- Linux/KVM behavior and production-like host sizing;
- cross-host cold restore and its latency;
- S3 upload, incremental archive chains and PostgreSQL commit semantics;
- the Windows named-pipe or Linux Unix-socket host relay;
- long-running code, cancellation under load and concurrent VM fleets;
- image upgrades, snapshot compatibility and repeated restore soak tests.

A successful Windows spike does not replace the Linux production spike.
