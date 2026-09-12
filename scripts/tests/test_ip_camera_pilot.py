import io
import sys

import pytest
from PIL import Image

from scripts import ip_camera_pilot


def jpeg_bytes(size=(16, 12)):
    output = io.BytesIO()
    Image.new("RGB", size, "green").save(output, format="JPEG")
    return output.getvalue()


def multipart_body(images):
    return b"".join(
        b"--video boundary--\r\nContent-Type: image/jpeg\r\n\r\n" + image + b"\r\n"
        for image in images
    ) + b"--video boundary----\r\n"


CONTENT_TYPE = "multipart/x-mixed-replace;boundary=video boundary--"


class FragmentedStream(io.BytesIO):
    def read1(self, size=-1):
        return super().read1(1)


def test_fragmented_multipart_collects_complete_frames():
    image = jpeg_bytes()
    frames = ip_camera_pilot.collect_frames(FragmentedStream(multipart_body([image, image])), CONTENT_TYPE, 2)
    assert frames == [image, image]


def test_yields_first_frame_before_reading_entire_capture():
    image = jpeg_bytes()
    body = multipart_body([image, image])
    stream = FragmentedStream(body)
    frames = ip_camera_pilot.iter_frames(stream, CONTENT_TYPE, 2)
    assert next(frames) == image
    assert stream.tell() < len(body) - len(image)
    assert list(frames) == [image]


def test_ignores_incomplete_tail_after_target():
    image = jpeg_bytes()
    body = multipart_body([image]).replace(b"--video boundary----\r\n", b"--video boundary--\r\n")
    body += b"Content-Type: image/jpeg\r\n\r\n\xff\xd8"
    assert ip_camera_pilot.collect_frames(io.BytesIO(body), CONTENT_TYPE, 1) == [image]


def test_rejects_truncated_stream_before_target():
    body = multipart_body([jpeg_bytes()])[:-40]
    with pytest.raises(ValueError, match="before enough complete frames"):
        ip_camera_pilot.collect_frames(io.BytesIO(body), CONTENT_TYPE, 1)


@pytest.mark.parametrize("content_type", ["text/html", "multipart/x-mixed-replace"])
def test_rejects_non_mjpeg_response(content_type):
    with pytest.raises(ValueError, match="with a boundary"):
        ip_camera_pilot.collect_frames(io.BytesIO(), content_type, 1)


def test_enforces_capture_size_limit():
    with pytest.raises(ValueError, match="byte limit"):
        ip_camera_pilot.collect_frames(io.BytesIO(multipart_body([jpeg_bytes()])), CONTENT_TYPE, 1, max_bytes=10)


def test_rejects_invalid_jpeg():
    with pytest.raises(OSError):
        ip_camera_pilot.collect_frames(io.BytesIO(multipart_body([b"not a JPEG"])), CONTENT_TYPE, 1)


def test_rejects_resolution_change():
    images = [jpeg_bytes(), jpeg_bytes((20, 16))]
    with pytest.raises(ValueError, match="dimensions changed"):
        ip_camera_pilot.collect_frames(io.BytesIO(multipart_body(images)), CONTENT_TYPE, 2)


def test_enforces_deadline(monkeypatch):
    moments = iter([0, 0, 26])
    monkeypatch.setattr(ip_camera_pilot.time, "monotonic", lambda: next(moments))
    with pytest.raises(TimeoutError, match="deadline"):
        ip_camera_pilot.collect_frames(io.BytesIO(multipart_body([jpeg_bytes()])), CONTENT_TYPE, 1)


@pytest.mark.parametrize("deadline_expired", [False, True])
def test_encoder_cleans_up_on_failure(monkeypatch, tmp_path, deadline_expired):
    class Process:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.killed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stdin.close()

        def kill(self):
            self.killed = True

        def poll(self):
            return -1 if self.killed else None

        def wait(self, timeout=None):
            return -1 if self.killed else 0

    class Watchdog:
        def __init__(self, timeout, callback):
            self.callback = callback
            self.cancelled = False

        def start(self):
            if deadline_expired:
                self.callback()

        def cancel(self):
            self.cancelled = True

    process = Process()
    watchdog = Watchdog(1, process.kill)
    monkeypatch.setattr(ip_camera_pilot, "media_tool", lambda name: name)
    monkeypatch.setattr(ip_camera_pilot.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(ip_camera_pilot.threading, "Timer", lambda *args: watchdog)

    def source():
        yield jpeg_bytes()
        assert process.stdin.tell() > 0
        if not deadline_expired:
            raise ValueError("Source disconnected")

    with pytest.raises((ValueError, RuntimeError)):
        ip_camera_pilot.encode_hls(source(), tmp_path, 30, "libx264", frame_count=1)
    assert process.killed
    assert process.stdin.closed
    assert watchdog.cancelled


def test_password_pipe_reads_utf8_without_echo(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"test-only-placeholder\n")))
    assert ip_camera_pilot.camera_password(from_stdin=True) == "test-only-placeholder"
    assert capsys.readouterr().out == ""


def test_camera_password_accepts_windows_private_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"test-only-placeholder\r\n")))
    assert ip_camera_pilot.camera_password(from_stdin=True) == "test-only-placeholder"


@pytest.mark.parametrize("payload", [b"", b"\n", b"missing-newline", b"x" * 4096 + b"\n"])
def test_password_pipe_rejects_invalid_input(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
    with pytest.raises(RuntimeError):
        ip_camera_pilot.camera_password(from_stdin=True)


@pytest.mark.parametrize("url", ["http://user:password@127.0.0.1/", "http://127.0.0.1/?password=example", "file:///camera", "http://127.0.0.1/#fragment"])
def test_camera_url_rejects_credentials_and_other_schemes(url):
    with pytest.raises(ValueError):
        ip_camera_pilot.validate_camera_url(url)


@pytest.mark.parametrize("source_fps", [10, 30])
def test_realtime_frames_preserve_capture_duration(monkeypatch, source_fps):
    clock = [100.0]
    monkeypatch.setattr(ip_camera_pilot.time, "monotonic", lambda: clock[0])

    def source():
        for index in range(source_fps * 4 + 1):
            clock[0] = 100 + index / source_fps
            yield str(index).encode()

    observed = {}
    frames = list(ip_camera_pilot.realtime_frames(source(), 15, 4, observed))
    assert len(frames) == 60
    assert frames[0] == b"0"
    assert int(frames[-1]) >= source_fps * 3.8
    assert observed["capture_seconds"] == pytest.approx(4)
    assert observed["received_frames"] == source_fps * 4 + 1


def test_realtime_frames_reject_premature_eof(monkeypatch):
    monkeypatch.setattr(ip_camera_pilot.time, "monotonic", lambda: 0)
    with pytest.raises(ValueError, match="before capture duration"):
        list(ip_camera_pilot.realtime_frames(iter([b"frame"] * 120), 15, 4, {}))


@pytest.mark.parametrize("moments", [
    [0, 0.04, 0.16, 0.21, 0.31, 0.4],
    [0, 0.1, 0.2, 0.3, 0.4],
])
def test_realtime_frames_use_last_received_frame_at_each_tick(monkeypatch, moments):
    clock = [0.0]
    monkeypatch.setattr(ip_camera_pilot.time, "monotonic", lambda: clock[0])

    def source():
        for index, moment in enumerate(moments):
            clock[0] = moment
            yield str(index).encode()

    assert list(ip_camera_pilot.realtime_frames(source(), 10, 0.4, {})) == [b"0", b"1", b"2", b"3"]