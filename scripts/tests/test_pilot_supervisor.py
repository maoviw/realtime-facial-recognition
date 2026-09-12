import json
import subprocess
import sys
from types import SimpleNamespace

import psutil
import pytest

from scripts import pilot_supervisor
from scripts.pilot_storage import StorageBudget


def reports(root):
    return [json.loads(path.read_text()) for path in root.glob("supervision/*/report.json")]


def test_supervision_retries_then_completes(tmp_path, monkeypatch):
    outcomes = iter([1, 0, 0])
    monkeypatch.setattr(pilot_supervisor, "run_attempt", lambda *args: next(outcomes))
    monkeypatch.setattr(pilot_supervisor.time, "sleep", lambda delay: None)
    result = pilot_supervisor.supervise([], StorageBudget(tmp_path, reserve_bytes=0), cycles=2)
    assert result["state"] == "completed"
    assert result["completed_cycles"] == 2
    assert result["storage_observed"]["checks"] >= 1
    assert result["elapsed_seconds"] >= 0
    assert result["storage_policy"]["quota_bytes"] == 512 * 1024 * 1024
    assert [attempt["state"] for attempt in result["attempts"]] == ["failed", "completed", "completed"]
    assert reports(tmp_path) == [result]
    assert not (tmp_path / ".pilot-writer.lock").exists()


def test_supervision_bounds_repeated_timeouts(tmp_path, monkeypatch):
    def timed_out(*args):
        raise TimeoutError()

    monkeypatch.setattr(pilot_supervisor, "run_attempt", timed_out)
    monkeypatch.setattr(pilot_supervisor.time, "sleep", lambda delay: None)
    with pytest.raises(RuntimeError, match="retry limit"):
        pilot_supervisor.supervise([], StorageBudget(tmp_path, reserve_bytes=0), cycles=1, retries=1)
    report = reports(tmp_path)[0]
    assert report["state"] == "stopped"
    assert report["completed_cycles"] == 0
    assert len(report["attempts"]) == 2
    assert all(attempt["state"] == "timed_out" for attempt in report["attempts"])


def test_duration_allows_more_than_100_cycles(tmp_path, monkeypatch):
    clock = [0]

    def capture(*args):
        clock[0] += 1
        return 0

    monkeypatch.setattr(pilot_supervisor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(pilot_supervisor, "run_attempt", capture)
    result = pilot_supervisor.supervise(
        [], StorageBudget(tmp_path, reserve_bytes=0), cycles=None, run_seconds=105,
    )
    assert result["completed_cycles"] == 105
    assert result["requested_cycles"] is None
    assert result["requested_seconds"] == 105
    assert result["stop_reason"] == "duration_reached"
    assert result["state"] == "completed"
    assert reports(tmp_path) == [result]


def test_duration_finishes_current_attempt_without_retry(tmp_path, monkeypatch):
    clock = [0]

    def capture(*args):
        clock[0] += 4
        return 1

    monkeypatch.setattr(pilot_supervisor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(pilot_supervisor.time, "sleep", lambda delay: None)
    monkeypatch.setattr(pilot_supervisor, "run_attempt", capture)
    result = pilot_supervisor.supervise(
        [], StorageBudget(tmp_path, reserve_bytes=0), cycles=None, run_seconds=2, retries=2,
    )
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["state"] == "failed"
    assert result["completed_cycles"] == 0
    assert result["elapsed_seconds"] == 4
    assert result["stop_reason"] == "duration_reached"


@pytest.mark.parametrize("preexisting", [False, True])
def test_stop_file_preserved_and_prevents_next_capture(tmp_path, monkeypatch, preexisting):
    stop_file = tmp_path / "stop.request"
    if preexisting:
        stop_file.touch()

    def capture(*args):
        assert not preexisting
        stop_file.touch()
        return 0

    monkeypatch.setattr(pilot_supervisor, "run_attempt", capture)
    result = pilot_supervisor.supervise(
        [], StorageBudget(tmp_path, reserve_bytes=0), cycles=None, run_seconds=86400, stop_file=stop_file,
    )
    assert result["completed_cycles"] == (0 if preexisting else 1)
    assert len(result["attempts"]) == result["completed_cycles"]
    assert result["stop_reason"] == "stop_file"
    assert result["state"] == "completed"
    assert stop_file.exists()
    assert not (tmp_path / ".pilot-writer.lock").exists()


@pytest.mark.parametrize("arguments", [
    ["--run-seconds", "0"], ["--run-seconds", "86401"],
    ["--run-seconds", "60", "--cycles", "2"],
])
def test_cli_rejects_invalid_session_limit(tmp_path, monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", [
        "supervisor", "--url", "http://127.0.0.1/video/mjpg.cgi", "--output-root", str(tmp_path), *arguments,
    ])
    monkeypatch.setattr(pilot_supervisor, "camera_password", lambda: pytest.fail("Password requested"))
    with pytest.raises(SystemExit) as error:
        pilot_supervisor.main()
    assert error.value.code == 2


def test_cli_rejects_existing_stop_file_before_password(tmp_path, monkeypatch):
    stop_file = tmp_path / "stop.request"
    stop_file.touch()
    monkeypatch.setattr(sys, "argv", [
        "supervisor", "--url", "http://127.0.0.1/video/mjpg.cgi", "--stop-file", str(stop_file),
    ])
    monkeypatch.setattr(pilot_supervisor, "camera_password", lambda: pytest.fail("Password requested"))
    with pytest.raises(SystemExit) as error:
        pilot_supervisor.main()
    assert error.value.code == 2
    assert stop_file.exists()


def test_storage_failure_is_not_retried(tmp_path, monkeypatch):
    def exhausted(*args):
        raise RuntimeError("quota")

    monkeypatch.setattr(pilot_supervisor, "run_attempt", exhausted)
    with pytest.raises(RuntimeError, match="quota"):
        pilot_supervisor.supervise([], StorageBudget(tmp_path, reserve_bytes=0), retries=3)
    assert len(reports(tmp_path)[0]["attempts"]) == 1
    assert reports(tmp_path)[0]["attempts"][0]["state"] == "aborted"
    assert not (tmp_path / ".pilot-writer.lock").exists()


def test_real_child_output_and_exit_code(tmp_path):
    budget = StorageBudget(tmp_path, reserve_bytes=0)
    assert pilot_supervisor.run_attempt(
        [sys.executable, "-c", "print('local child')"], budget, tmp_path / "child.log", 10,
    ) == 0
    assert "local child" in (tmp_path / "child.log").read_text()


def test_metrics_track_tree_and_keep_exited_child_cpu(monkeypatch):
    class Process:
        def __init__(self, pid, cpu, rss):
            self.pid, self.cpu, self.rss = pid, cpu, rss
            self.active = True

        def create_time(self):
            return 100 + self.pid

        def is_running(self):
            return self.active

        def children(self, recursive):
            assert recursive
            return [child] if child.active else []

        def cpu_times(self):
            return SimpleNamespace(user=self.cpu, system=0)

        def memory_info(self):
            return SimpleNamespace(rss=self.rss)

    root, child = Process(1, 1, 100), Process(2, 3, 200)
    monkeypatch.setattr(psutil, "Process", lambda pid: root)
    moments = iter([0, 2, 4])
    monkeypatch.setattr(pilot_supervisor.time, "monotonic", lambda: next(moments))
    observed = {}
    monitor = pilot_supervisor.ProcessMetrics(1, observed)
    monitor.sample()
    assert observed["cpu_percent_one_core"] == 200
    child.active = False
    root.cpu = 2
    monitor.sample()
    assert observed == {
        "samples": 2, "unavailable_samples": 0, "peak_rss_bytes": 300,
        "cpu_seconds_observed": 5, "elapsed_seconds": 4, "cpu_percent_one_core": 125,
    }


def test_metrics_tolerate_process_disappearing(monkeypatch):
    def gone(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", gone)
    observed = {}
    monitor = pilot_supervisor.ProcessMetrics(123, observed)
    monitor.sample()
    assert observed["unavailable_samples"] == 1
    assert observed["cpu_seconds_observed"] == 0
    assert observed["peak_rss_bytes"] == 0


def test_supervision_persists_optional_metrics(tmp_path):
    result = pilot_supervisor.supervise(
        [sys.executable, "-c", "sum(range(1000000))"], StorageBudget(tmp_path, reserve_bytes=0),
        cycles=1, collect_metrics=True,
    )
    observed = result["attempts"][0]["resources"]
    assert observed["samples"] >= 1
    assert observed["elapsed_seconds"] >= 0
    assert reports(tmp_path) == [result]


def test_attempt_kills_blocked_child(tmp_path, monkeypatch):
    class Process:
        returncode = None
        stdin = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("capture", timeout)

    process = Process()
    moments = iter([0, 2])
    monkeypatch.setattr(pilot_supervisor.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(pilot_supervisor.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(pilot_supervisor, "stop_process_tree", lambda child: setattr(child, "returncode", -1))
    with pytest.raises(TimeoutError):
        pilot_supervisor.run_attempt([], StorageBudget(tmp_path, reserve_bytes=0), tmp_path / "child.log", 1)
    assert process.returncode == -1


def test_deadline_stops_real_process(tmp_path, monkeypatch):
    original_popen = subprocess.Popen
    children = []

    def record_child(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(pilot_supervisor.subprocess, "Popen", record_child)
    with pytest.raises(TimeoutError):
        pilot_supervisor.run_attempt(
            [sys.executable, "-c", "import threading; threading.Event().wait(30)"],
            StorageBudget(tmp_path, reserve_bytes=0), tmp_path / "blocked.log", 1,
        )
    assert children[0].poll() is not None


def test_disk_exhaustion_stops_running_process(tmp_path, monkeypatch):
    class Process:
        returncode = None
        stdin = None

        def poll(self):
            return self.returncode

    process = Process()
    budget = StorageBudget(tmp_path, reserve_bytes=0)
    checks = iter([None, RuntimeError("reserve reached")])

    def check():
        error = next(checks)
        if error:
            raise error

    monkeypatch.setattr(budget, "check", check)
    monkeypatch.setattr(pilot_supervisor.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(pilot_supervisor, "stop_process_tree", lambda child: setattr(child, "returncode", -1))
    with pytest.raises(RuntimeError, match="reserve"):
        pilot_supervisor.run_attempt([], budget, tmp_path / "child.log", 10)
    assert process.returncode == -1


def test_preflight_quota_never_launches_process(tmp_path, monkeypatch):
    (tmp_path / "existing.m4s").write_bytes(b"x" * 30)
    monkeypatch.setattr(pilot_supervisor.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Capture started"))
    with pytest.raises(RuntimeError, match="quota"):
        pilot_supervisor.supervise([], StorageBudget(tmp_path, 40, 0, 20))
    assert (tmp_path / "existing.m4s").stat().st_size == 30
    assert not reports(tmp_path)


def test_interruption_is_recorded_and_unlocks_storage(tmp_path, monkeypatch):
    def interrupt(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(pilot_supervisor, "run_attempt", interrupt)
    with pytest.raises(KeyboardInterrupt):
        pilot_supervisor.supervise([], StorageBudget(tmp_path, reserve_bytes=0))
    assert reports(tmp_path)[0]["state"] == "stopped"
    assert reports(tmp_path)[0]["error_type"] == "KeyboardInterrupt"
    assert not (tmp_path / ".pilot-writer.lock").exists()


def test_private_input_never_appears_in_report_or_log(tmp_path):
    secret = b"test-only-placeholder\n"
    command = [sys.executable, "-c", "import sys; assert sys.stdin.buffer.readline(); print('received')"]
    result = pilot_supervisor.supervise(
        command, StorageBudget(tmp_path, reserve_bytes=0), cycles=1, private_input=secret,
    )
    assert result["state"] == "completed"
    for path in (tmp_path / "supervision").rglob("*"):
        if path.is_file():
            assert secret.strip() not in path.read_bytes()


@pytest.mark.parametrize("run_seconds", [None, 120])
@pytest.mark.parametrize("password_stdin", [False, True])
def test_live_command_uses_private_pipe(tmp_path, monkeypatch, capsys, run_seconds, password_stdin):
    secret = "test-only-placeholder"
    captured = {}
    limit_arguments = [] if run_seconds is None else ["--run-seconds", str(run_seconds)]
    password_arguments = ["--password-stdin"] if password_stdin else []
    monkeypatch.setattr(sys, "argv", [
        "supervisor", "--url", "http://127.0.0.1/video/mjpg.cgi", "--output-root", str(tmp_path),
        *limit_arguments, *password_arguments,
    ])
    monkeypatch.setattr(pilot_supervisor, "camera_password", lambda from_stdin: secret if from_stdin == password_stdin else pytest.fail("Wrong password source"))
    monkeypatch.setattr(pilot_supervisor, "supervise", lambda command, *args, **kwargs: captured.update(command=command, cycles=args[1], **kwargs))
    pilot_supervisor.main()
    assert captured["cycles"] == (3 if run_seconds is None else None)
    assert captured["run_seconds"] == run_seconds
    assert "--password-stdin" in captured["command"]
    assert secret not in " ".join(captured["command"])
    assert captured["private_input"] == (secret + "\n").encode()
    assert secret not in capsys.readouterr().out