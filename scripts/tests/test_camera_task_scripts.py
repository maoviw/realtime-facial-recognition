import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.skipif(os.name != "nt", reason="Windows scheduled-task scripts")
def test_camera_task_recovers_and_stops_without_installing(tmp_path):
    powershell = shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell 7 is not installed")

    state_root = tmp_path / "state"
    output_root = tmp_path / "output"
    install_script = PROJECT_ROOT / "scripts" / "install-camera-task.ps1"
    run_script = PROJECT_ROOT / "scripts" / "run-camera-task.ps1"
    command = (
        f"$secret = ConvertTo-SecureString 'test-only-placeholder' -AsPlainText -Force; "
        f"& {ps_literal(install_script)} -Url 'http://127.0.0.1/video/mjpg.cgi' "
        f"-StateRoot {ps_literal(state_root)} -OutputRoot {ps_literal(output_root)} "
        "-RunSeconds 1 -RestartDelaySeconds 1 -CameraPassword $secret -PrepareOnly"
    )
    prepared = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    paths = json.loads(prepared.stdout)

    fake_project = tmp_path / "project"
    fake_scripts = fake_project / "scripts"
    fake_scripts.mkdir(parents=True)
    (fake_scripts / "__init__.py").write_text("", encoding="utf-8")
    (fake_scripts / "pilot_supervisor.py").write_text(
        """import argparse
from pathlib import Path
import sys

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--stop-file')
args, _ = parser.parse_known_args()
password = sys.stdin.readline().rstrip('\\r\\n')
counter = Path(args.stop_file).with_name('fake-session-count')
session = int(counter.read_text() if counter.exists() else '0') + 1
counter.write_text(str(session))
print(f'session={session} password_ok={password == "test-only-placeholder"}')
if session == 1:
    raise SystemExit(7)
Path(args.stop_file).touch()
""",
        encoding="utf-8",
    )
    config_path = Path(paths["ConfigPath"])
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    config["ProjectRoot"] = str(fake_project)
    config["PythonPath"] = sys.executable
    config_path.write_text(json.dumps(config), encoding="utf-8")

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(run_script),
            "-ConfigPath",
            str(config_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    status = json.loads(Path(config["StatusPath"]).read_text(encoding="utf-8-sig"))
    log = Path(config["LogPath"]).read_text(encoding="utf-8-sig")
    assert completed.returncode == 0
    assert status["State"] == "stopped"
    assert status["Sessions"] == 2
    assert "session=1 exit_code=7" in log
    assert "session=2 exit_code=0" in log
    assert log.count("password_ok=True") == 2
    assert "test-only-placeholder" not in log


@pytest.mark.skipif(os.name != "nt", reason="Windows scheduled-task scripts")
def test_camera_task_rejects_corrupt_secret_without_launching(tmp_path):
    powershell = shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell 7 is not installed")

    state_root = tmp_path / "state"
    install_script = PROJECT_ROOT / "scripts" / "install-camera-task.ps1"
    run_script = PROJECT_ROOT / "scripts" / "run-camera-task.ps1"
    command = (
        f"$secret = ConvertTo-SecureString 'test-only-placeholder' -AsPlainText -Force; "
        f"& {ps_literal(install_script)} -Url 'http://127.0.0.1/video/mjpg.cgi' "
        f"-StateRoot {ps_literal(state_root)} -OutputRoot {ps_literal(tmp_path / 'output')} "
        "-CameraPassword $secret -PrepareOnly"
    )
    prepared = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    paths = json.loads(prepared.stdout)
    Path(paths["SecretPath"]).write_text("not-dpapi", encoding="utf-8")

    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(run_script),
         "-ConfigPath", paths["ConfigPath"], "-ValidateOnly"],
        check=False,
        capture_output=True,
        text=True,
    )

    status = json.loads((state_root / "camera-task-status.json").read_text(encoding="utf-8-sig"))
    assert completed.returncode != 0
    assert status["State"] == "failed"
    assert status["ErrorType"] in {"FormatException", "PSArgumentException"}
    assert "test-only-placeholder" not in completed.stdout + completed.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows scheduled-task scripts")
def test_stop_script_rejects_path_outside_state(tmp_path):
    powershell = shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell 7 is not installed")

    state_root = tmp_path / "state"
    state_root.mkdir()
    outside = tmp_path / "outside.stop"
    (state_root / "camera-task.json").write_text(
        json.dumps({"StopFile": str(outside)}), encoding="utf-8"
    )
    stop_script = PROJECT_ROOT / "scripts" / "stop-camera-task.ps1"
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(stop_script),
         "-StateRoot", str(state_root)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert not outside.exists()