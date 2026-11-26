#!/usr/bin/env python3
"""Cross-platform microsandbox full-snapshot spike.

Run with the host Python after installing the ``msb`` CLI. The script uses
only the standard library and removes its sandboxes/snapshot group on exit
unless ``--keep`` is supplied.
"""

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


class SpikeFailure(RuntimeError):
    pass


def execute(argv: list[str], timeout: int, isolated_output: bool = False) -> subprocess.CompletedProcess[str]:
    """Run msb without letting a detached VM keep output pipes open.

    VM-launching commands use files because their child can inherit pipes on
    Windows. Ordinary commands retain normal pipe capture for reliable output.
    """
    if not isolated_output:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        completed = subprocess.run(
            argv,
            stdout=stdout,
            stderr=stderr,
            timeout=timeout,
            check=False,
        )
        stdout.seek(0)
        stderr.seek(0)
        stdout_text = stdout.read().decode("utf-8", errors="replace")
        stderr_text = stderr.read().decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(argv, completed.returncode, stdout_text, stderr_text)


class Spike:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.msb = shutil.which("msb")
        if not self.msb:
            raise SpikeFailure("msb was not found on PATH")
        self.run_id = f"{int(time.time())}-{platform.system().lower()}"
        self.source = f"msb-spike-source-{self.run_id}"
        self.restored = f"msb-spike-restored-{self.run_id}"
        self.rollback = f"msb-spike-rollback-{self.run_id}"
        self.group = f"msb-spike-group-{self.run_id}"
        self.guest_script = Path(__file__).with_name("guest_state.py").resolve()
        self.network_script = Path(__file__).with_name("guest_network_probe.py").resolve()
        self.launch_script = Path(__file__).with_name("guest_launch.py").resolve()
        self.results = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "host_platform": platform.platform(),
            "host_python": platform.python_version(),
            "image": args.image,
            "source": self.source,
            "restored": self.restored,
            "rollback": self.rollback,
            "snapshot_group": self.group,
        }

    def command(self, *arguments: str, timeout: int = 120, quiet: bool = False) -> subprocess.CompletedProcess[str]:
        argv = [self.msb, *arguments]
        completed = execute(argv, timeout, isolated_output=arguments[0] == "create")
        if not quiet:
            print(f"$ msb {' '.join(arguments)}", flush=True)
            if completed.stdout.strip():
                print(completed.stdout.strip(), flush=True)
            if completed.stderr.strip():
                print(completed.stderr.strip(), file=sys.stderr, flush=True)
            print(f"exit={completed.returncode}", flush=True)
        if completed.returncode != 0 or completed.stderr.lstrip().startswith("error:"):
            raise SpikeFailure(
                f"Command failed ({completed.returncode}): {' '.join(arguments)}: {completed.stderr.strip()}"
            )
        return completed

    def wait_for_running(self, sandbox: str, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_status = "not found"
        while time.monotonic() < deadline:
            completed = execute([self.msb, "inspect", sandbox], timeout=15)
            output = completed.stdout + completed.stderr
            if "Status:" in output and "running" in output.lower():
                return
            last_status = output.strip() or last_status
            time.sleep(0.1)
        raise SpikeFailure(f"Sandbox {sandbox} did not become running: {last_status}")

    def wait_for_text(self, sandbox: str, path: str, timeout_seconds: float) -> str:
        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            completed = execute(
                [self.msb, "exec", sandbox, "--no-stdin", "--", "cat", path],
                timeout=15,
            )
            text = completed.stdout.strip()
            if completed.returncode == 0 and text:
                return text
            last_error = completed.stderr.strip()
            time.sleep(0.25)
        raise SpikeFailure(f"Timed out waiting for {path} in {sandbox}: {last_error}")

    def run(self) -> dict:
        version = self.command("--version").stdout.strip()
        self.results["msb_version"] = version

        started = time.perf_counter()
        self.command(
            "create",
            "--name",
            self.source,
            "--memory",
            self.args.memory,
            "--no-net",
            "--copy-file",
            f"{self.guest_script}:/tmp/guest_state.py",
            "--copy-file",
            f"{self.network_script}:/tmp/guest_network_probe.py",
            "--copy-file",
            f"{self.launch_script}:/tmp/guest_launch.py",
            self.args.image,
            timeout=self.args.operation_timeout,
        )
        self.wait_for_running(self.source, 60)
        self.results["create_ms"] = round((time.perf_counter() - started) * 1000, 1)

        python_version = self.command(
            "exec", self.source, "--no-stdin", "--", "python", "--version"
        ).stdout.strip()
        self.results["guest_python_version"] = python_version

        network_result = self.command(
            "exec", self.source, "--no-stdin", "--", "python", "/tmp/guest_network_probe.py",
            timeout=30,
        ).stdout.strip()
        self.results["network_probe"] = network_result
        if "NETWORK_BLOCKED" not in network_result:
            raise SpikeFailure(f"Network isolation probe failed: {network_result!r}")

        self.command(
            "exec", self.source, "--no-stdin", "--", "python", "/tmp/guest_launch.py",
        )
        started_text = self.wait_for_text(self.source, "/tmp/guest-state-started.json", 30)
        self.results["before_snapshot"] = json.loads(started_text)

        started = time.perf_counter()
        self.command(
            "snap", "create", self.group,
            "--sandbox", self.source,
            "--name", "state",
            "--full",
            "--integrity",
            timeout=self.args.operation_timeout,
        )
        self.results["snapshot_ms"] = round((time.perf_counter() - started) * 1000, 1)

        # Production resumes a parked VM; it does not run two clones together.
        self.command("rm", "--force", self.source)
        self.results["source_removed_before_restore"] = True

        started = time.perf_counter()
        self.command(
            "create",
            "--from-snapshot", self.group,
            "--checkpoint", "state",
            "--name", self.restored,
            timeout=self.args.operation_timeout,
        )
        self.wait_for_running(self.restored, 120)
        self.results["restore_ms"] = round((time.perf_counter() - started) * 1000, 1)

        self.command("exec", self.restored, "--no-stdin", "--", "touch", "/tmp/guest-state-resume")
        result_text = self.wait_for_text(self.restored, "/tmp/guest-state-result.json", 30)
        restored_state = json.loads(result_text)
        self.results["restored_state"] = restored_state
        expected = {
            "value": 42,
            "message": "resident-in-python-memory",
            "disk": "disk-before-snapshot",
        }
        if restored_state != expected:
            raise SpikeFailure(f"Restored state mismatch: expected {expected}, got {restored_state}")

        # Simulate cancellation: post-snapshot disk changes are discarded and
        # the same immutable pre-task snapshot is resumed sequentially.
        self.command("exec", self.restored, "--no-stdin", "--", "touch", "/tmp/cancelled-task")
        self.command("rm", "--force", self.restored)
        started = time.perf_counter()
        rollback_create = [
            "create", "--from-snapshot", self.group,
            "--checkpoint", "state", "--name", self.rollback,
        ]
        if self.args.cow_memory:
            rollback_create.append("--cow-mem")
        self.command(*rollback_create, timeout=self.args.operation_timeout)
        self.wait_for_running(self.rollback, 120)
        self.results["rollback_ms"] = round((time.perf_counter() - started) * 1000, 1)
        marker = execute(
            [self.msb, "exec", self.rollback, "--no-stdin", "--", "stat", "/tmp/cancelled-task"],
            timeout=15,
        )
        if marker.stdout.strip():
            raise SpikeFailure("Cancelled-task marker survived snapshot rollback")
        self.command("exec", self.rollback, "--no-stdin", "--", "touch", "/tmp/guest-state-resume")
        rollback_text = self.wait_for_text(self.rollback, "/tmp/guest-state-result.json", 30)
        rollback_state = json.loads(rollback_text)
        if rollback_state != expected:
            raise SpikeFailure(f"Rollback state mismatch: expected {expected}, got {rollback_state}")
        self.results["rollback_state"] = rollback_state
        self.results["rollback_passed"] = True

        self.results["passed"] = True
        return self.results

    def cleanup(self) -> None:
        if self.args.keep:
            return
        for arguments in (
            ("rm", "--force", self.source, self.restored, self.rollback),
            ("snap", "rm", self.group, "--all"),
        ):
            try:
                execute([self.msb, *arguments], timeout=60)
            except Exception as error:
                print(f"Cleanup warning for {' '.join(arguments)}: {error}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="python:3.12")
    parser.add_argument("--memory", default="512M")
    parser.add_argument("--operation-timeout", type=int, default=900)
    parser.add_argument("--keep", action="store_true", help="Keep sandboxes and snapshots for inspection")
    parser.add_argument("--cow-memory", action="store_true", help="Use CoW memory for the rollback restore")
    parser.add_argument("--results", type=Path, help="Write a JSON result report to this path")
    args = parser.parse_args()

    spike = Spike(args)
    try:
        results = spike.run()
    except Exception as error:
        spike.results["passed"] = False
        spike.results["error"] = f"{type(error).__name__}: {error}"
        print(spike.results["error"], file=sys.stderr, flush=True)
        results = spike.results
        exit_code = 1
    else:
        exit_code = 0
    finally:
        spike.cleanup()
        results = spike.results
        results["finished_at"] = datetime.now(timezone.utc).isoformat()
        if args.results:
            args.results.parent.mkdir(parents=True, exist_ok=True)
            args.results.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"Results: {args.results}", flush=True)

    print(json.dumps(results, indent=2, ensure_ascii=False), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
