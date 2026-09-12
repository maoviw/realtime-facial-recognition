import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

from scripts.ip_camera_pilot import camera_password, validate_camera_url
from scripts.pilot_storage import MIB, StorageBudget


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProcessMetrics:
    def __init__(self, pid, observed):
        import psutil

        self.psutil = psutil
        self.observed = observed
        self.started = time.monotonic()
        self.processes = {}
        self.cpu_totals = {}
        observed.update(samples=0, unavailable_samples=0, peak_rss_bytes=0, cpu_seconds_observed=0.0,
                        elapsed_seconds=0.0, cpu_percent_one_core=0.0)
        try:
            self.root = psutil.Process(pid)
        except psutil.Error:
            self.root = None
            observed["unavailable_samples"] += 1

    def sample(self):
        if self.root is not None:
            try:
                if self.root.is_running():
                    for process in [self.root, *self.root.children(recursive=True)]:
                        self.processes[(process.pid, process.create_time())] = process
            except self.psutil.Error:
                self.observed["unavailable_samples"] += 1
        rss_bytes = 0
        for identity, process in list(self.processes.items()):
            try:
                if not process.is_running():
                    del self.processes[identity]
                    continue
                cpu = process.cpu_times()
                self.cpu_totals[identity] = max(self.cpu_totals.get(identity, 0.0), cpu.user + cpu.system)
                rss_bytes += process.memory_info().rss
            except self.psutil.Error:
                self.observed["unavailable_samples"] += 1
        elapsed = time.monotonic() - self.started
        cpu_seconds = sum(self.cpu_totals.values())
        self.observed.update(
            samples=self.observed["samples"] + 1,
            peak_rss_bytes=max(self.observed["peak_rss_bytes"], rss_bytes),
            cpu_seconds_observed=round(cpu_seconds, 3), elapsed_seconds=round(elapsed, 3),
            cpu_percent_one_core=round(100 * cpu_seconds / elapsed, 2) if elapsed > 0 else 0.0,
        )


def stop_process_tree(process):
    if os.name == "nt":
        if process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10,
            )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def run_attempt(command, budget, log_path, timeout_seconds, private_input=None, resources=None):
    budget.check()
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with log_path.open("xb") as log:
        process = subprocess.Popen(
            command, cwd=PROJECT_ROOT, stdin=subprocess.PIPE if private_input is not None else subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, **options,
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            monitor = ProcessMetrics(process.pid, resources) if resources is not None else None
            if private_input is not None:
                process.stdin.write(private_input)
                process.stdin.close()
            while process.poll() is None:
                budget.check()
                if monitor is not None:
                    monitor.sample()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Capture exceeded its wall-clock deadline.")
                try:
                    process.wait(timeout=min(1, remaining))
                except subprocess.TimeoutExpired:
                    pass
            if monitor is not None:
                monitor.sample()
            budget.check()
            return process.returncode
        finally:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            finally:
                if process.poll() is None:
                    stop_process_tree(process)


def supervise(command, budget, cycles: int | None = 3, retries=2, timeout_seconds=90, private_input=None, collect_metrics=False,
              *, run_seconds: int | None = None, stop_file=None):
    valid_limit = (
        cycles is None and run_seconds is not None and 1 <= run_seconds <= 86400
        or run_seconds is None and cycles is not None and 1 <= cycles <= 100
    )
    if not valid_limit or not 0 <= retries <= 3 or timeout_seconds <= 0:
        raise ValueError("Invalid supervision limits.")
    stop_file = Path(stop_file).absolute() if stop_file is not None else None
    with budget:
        report_directory = budget.root / "supervision" / uuid.uuid4().hex
        report_directory.mkdir(parents=True)
        report_path = report_directory / "report.json"
        session_started = time.monotonic()
        report = {
            "state": "running", "started_at": datetime.now(timezone.utc).isoformat(),
            "requested_cycles": cycles, "requested_seconds": run_seconds,
            "stop_file": str(stop_file) if stop_file is not None else None,
            "completed_cycles": 0, "attempts": [],
            "storage_policy": {
                "quota_bytes": budget.quota_bytes, "reserve_bytes": budget.reserve_bytes,
                "headroom_bytes": budget.headroom_bytes,
            },
        }

        def save_report():
            report["elapsed_seconds"] = round(time.monotonic() - session_started, 3)
            report["storage_observed"] = dict(budget.observed)
            temporary = report_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
            temporary.replace(report_path)

        def stop_reason():
            if stop_file is not None and stop_file.exists():
                return "stop_file"
            if run_seconds is not None and time.monotonic() - session_started >= run_seconds:
                return "duration_reached"
            if cycles is not None and report["completed_cycles"] >= cycles:
                return "cycles_completed"
            return None

        save_report()
        try:
            cycle = 0
            while not (reason := stop_reason()):
                cycle += 1
                for retry in range(retries + 1):
                    if stop_reason():
                        break
                    log_path = report_directory / f"cycle-{cycle:03d}-attempt-{retry + 1}.log"
                    attempt = {"cycle": cycle, "attempt": retry + 1, "state": "running", "log": log_path.name}
                    report["attempts"].append(attempt)
                    save_report()
                    started = time.monotonic()
                    try:
                        resources = attempt.setdefault("resources", {}) if collect_metrics else None
                        exit_code = run_attempt(command, budget, log_path, timeout_seconds, private_input, resources)
                        attempt.update(state="completed" if exit_code == 0 else "failed", exit_code=exit_code)
                    except TimeoutError:
                        attempt.update(state="timed_out")
                    except BaseException as error:
                        attempt.update(state="aborted", error_type=type(error).__name__)
                        raise
                    finally:
                        attempt["elapsed_seconds"] = round(time.monotonic() - started, 3)
                        save_report()
                    print(json.dumps({"cycle": cycle, "attempt": retry + 1, "state": attempt["state"]}), flush=True)
                    if attempt["state"] == "completed":
                        report["completed_cycles"] += 1
                        save_report()
                        break
                    if attempt.get("exit_code") == 77:
                        raise RuntimeError("Camera authentication refused; no automatic retry.")
                    if retry == retries:
                        raise RuntimeError("Capture retry limit reached; inspect the local attempt logs.")
                    time.sleep(min(2 ** retry, 4))
            report.update(state="completed", stop_reason=reason)
        except BaseException as error:
            report.update(state="stopped", error_type=type(error).__name__)
            raise
        finally:
            save_report()
            print(json.dumps({"report": str(report_path), "state": report["state"], "completed_cycles": report["completed_cycles"]}), flush=True)
        return report


def main():
    parser = argparse.ArgumentParser(description="Bounded local video supervision; no deletion, no AI.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--usb", action="store_true")
    source.add_argument("--replay", type=Path)
    source.add_argument("--url")
    parser.add_argument("--content-type")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--device", default="Logitech Webcam C930e")
    parser.add_argument("--seconds", type=int, default=20)
    limit = parser.add_mutually_exclusive_group()
    limit.add_argument("--cycles", type=int)
    limit.add_argument("--run-seconds", type=int, help="Session duration (1..86400); finish the current clip before stopping.")
    parser.add_argument("--stop-file", type=Path, help="Stop after the current attempt when this file exists; never deleted.")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--encoder", choices=("libx264", "h264_nvenc"), default="libx264")
    parser.add_argument("--metrics", action="store_true", help="Sample capture process-tree CPU/RSS (requires psutil).")
    parser.add_argument("--quota-mib", type=int, default=512)
    parser.add_argument("--reserve-mib", type=int, default=2048)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "backend/data/phase0-pilots")
    args = parser.parse_args()
    if args.cycles is None and args.run_seconds is None:
        args.cycles = 3
    if not 4 <= args.seconds <= 60 or not 0 <= args.retries <= 3 or (args.cycles is not None and not 1 <= args.cycles <= 100):
        parser.error("seconds: 4..60; cycles: 1..100; retries: 0..3.")
    if args.run_seconds is not None and not 1 <= args.run_seconds <= 86400:
        parser.error("run-seconds: 1..86400.")
    if args.stop_file is not None and (args.stop_file.exists() or not args.stop_file.absolute().parent.is_dir()):
        parser.error("stop-file must not exist and its parent directory must exist.")
    if args.quota_mib <= 32 or args.reserve_mib < 0:
        parser.error("quota-mib must exceed 32; reserve-mib must be nonnegative.")
    root = args.output_root.absolute()
    budget = StorageBudget(root, args.quota_mib * MIB, args.reserve_mib * MIB)
    private_input = None
    if args.usb:
        powershell = shutil.which("pwsh")
        if os.name != "nt" or not powershell:
            parser.error("USB capture requires Windows and PowerShell 7.")
        command = [
            powershell, "-NoProfile", "-NonInteractive", "-File", str(PROJECT_ROOT / "scripts/test-usb-capture.ps1"),
            "-DeviceName", args.device, "-DurationSeconds", str(args.seconds),
            "-Encoder", args.encoder, "-OutputRoot", str(root),
        ]
    elif args.replay:
        if not args.replay.is_file() or not args.content_type:
            parser.error("Replay requires an existing file and --content-type.")
        command = [
            sys.executable, "-m", "scripts.ip_camera_pilot", "--replay", str(args.replay.resolve()),
            "--content-type", args.content_type, "--pace-replay", "--fps", "15",
            "--seconds", str(args.seconds), "--encoder", args.encoder, "--output-root", str(root),
        ]
    else:
        try:
            validate_camera_url(args.url)
        except ValueError:
            parser.error("Use an HTTP(S) camera URL without credentials, query or fragment.")
        budget.check()
        print("Local camera supervision. HTTP Basic is unencrypted; trusted LAN only.", flush=True)
        private_input = (camera_password(args.password_stdin) + "\n").encode("utf-8")
        command = [
            sys.executable, "-m", "scripts.ip_camera_pilot", "--url", args.url,
            "--username", args.username, "--password-stdin", "--fps", "15",
            "--seconds", str(args.seconds), "--encoder", args.encoder, "--output-root", str(root),
        ]
    supervise(command, budget, args.cycles, args.retries, timeout_seconds=30 + args.seconds * 3,
              private_input=private_input, collect_metrics=args.metrics,
              run_seconds=args.run_seconds, stop_file=args.stop_file)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f"Supervision stopped ({type(error).__name__}): {error}", file=sys.stderr)
        sys.exit(1)