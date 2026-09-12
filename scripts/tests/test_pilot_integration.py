import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import sys
import threading
import time

import pytest
from PIL import Image

from scripts import ip_camera_pilot, pilot_supervisor


@pytest.mark.parametrize("first_status", [503, 401, "disconnect", "stall", "stop", "duration"])
def test_authenticated_network_supervision_with_real_encoding(tmp_path, monkeypatch, first_status):
    try:
        ip_camera_pilot.media_tool("ffmpeg")
        ip_camera_pilot.media_tool("ffprobe")
    except RuntimeError:
        pytest.skip("FFmpeg and FFprobe required for local media integration.")
    password = "test-only-placeholder"
    authorization = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()
    image = io.BytesIO()
    Image.new("RGB", (320, 240), "green").save(image, format="JPEG")
    part = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + image.getvalue() + b"\r\n"
    received_auth = []
    stop = threading.Event()
    stop_file = tmp_path / "stop.request"

    class Camera(BaseHTTPRequestHandler):
        def do_GET(self):
            received_auth.append(self.headers.get("Authorization"))
            if received_auth[-1] != authorization:
                self.send_error(401)
            elif len(received_auth) == 1 and first_status in (503, 401):
                self.send_error(first_status)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                started = time.monotonic()
                try:
                    for index in range(180):
                        if first_status == "stop" and index == 90:
                            stop_file.touch()
                        if len(received_auth) == 1 and index == 90 and first_status in ("disconnect", "stall"):
                            if first_status == "stall":
                                stop.wait(4)
                            break
                        if stop.wait(max(0, started + index / 30 - time.monotonic())):
                            break
                        self.wfile.write(part)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    limit_arguments = ["--run-seconds", "1"] if first_status == "duration" else ["--cycles", "2"]
    monkeypatch.setattr(sys, "argv", [
        "supervisor", "--url", f"http://127.0.0.1:{server.server_port}/video/mjpg.cgi",
        "--seconds", "4", *limit_arguments, "--retries", "1", "--reserve-mib", "0",
        "--stop-file", str(stop_file),
        "--output-root", str(tmp_path),
    ])
    monkeypatch.setattr(
        pilot_supervisor,
        "camera_password",
        lambda from_stdin: password if not from_stdin else pytest.fail("Unexpected stdin password"),
    )
    try:
        if first_status == 401:
            with pytest.raises(RuntimeError, match="authentication refused"):
                pilot_supervisor.main()
        else:
            pilot_supervisor.main()
    finally:
        stop.set()
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
    report = json.loads(next(tmp_path.glob("supervision/*/report.json")).read_text())
    if first_status == 401:
        assert report["state"] == "stopped"
        assert report["completed_cycles"] == 0
        assert len(received_auth) == 1
        assert report["attempts"][0]["exit_code"] == 77
    else:
        graceful = first_status in ("stop", "duration")
        expected_captures = 1 if graceful else 2
        assert report["state"] == "completed"
        assert report["completed_cycles"] == expected_captures
        assert len(received_auth) == (1 if graceful else 3)
        expected_states = ["completed"] if graceful else ["failed", "completed", "completed"]
        assert [attempt["state"] for attempt in report["attempts"]] == expected_states
        if first_status == "stop":
            assert report["stop_reason"] == "stop_file"
            assert stop_file.exists()
        elif first_status == "duration":
            assert report["stop_reason"] == "duration_reached"
            assert report["elapsed_seconds"] >= 4
        playlists = list(tmp_path.glob("*/index.m3u8"))
        finalized = [playlist for playlist in playlists if "#EXT-X-ENDLIST" in playlist.read_text()]
        assert len(finalized) == expected_captures
        if first_status in ("disconnect", "stall"):
            assert len(playlists) == 3
            assert report["attempts"][0]["exit_code"] != 0
            assert report["attempts"][0]["elapsed_seconds"] < 12
        else:
            assert len(playlists) == expected_captures
        logs = list(tmp_path.glob("supervision/*/*.log"))
        captures = [json.loads("{" + log.read_text().partition("{")[2]) for log in logs if "\"capture_seconds\"" in log.read_text()]
        assert len(captures) == expected_captures
        for capture in captures:
            assert capture["frames"] == 60
            assert capture["received_frames"] > 100
            assert capture["capture_seconds"] == pytest.approx(4, abs=0.5)
            assert capture["playback_seconds"] == pytest.approx(4, abs=0.1)
            assert capture["elapsed_seconds"] >= capture["capture_seconds"]
            assert capture["published_before_eof"]
            assert capture["decode_verified"]
            assert capture["timing_mode"] == "monotonic-receive"
    assert all(value == authorization for value in received_auth)
    assert not (tmp_path / ".pilot-writer.lock").exists()
    for path in (tmp_path / "supervision").rglob("*"):
        if path.is_file():
            content = path.read_text()
            assert password not in content
            assert authorization not in content