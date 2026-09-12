import argparse
from collections import deque
import getpass
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid
import warnings

from multipart import MultipartParser
from multipart.multipart import parse_options_header
from PIL import Image
import requests


class CameraAuthenticationError(RuntimeError):
    pass


def iter_frames(stream, content_type, frame_count, timeout_seconds=25, max_bytes=32 * 1024 * 1024):
    media_type, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if media_type != b"multipart/x-mixed-replace" or not boundary or len(boundary) > 200:
        raise ValueError("Expected multipart/x-mixed-replace with a boundary.")
    if frame_count < 1 or timeout_seconds <= 0 or max_bytes < 1:
        raise ValueError("Capture limits must be positive.")

    frames = deque()
    accepted = 0
    part = bytearray()
    dimensions = None

    def part_begin():
        part.clear()

    def part_data(data, start, end):
        if len(part) + end - start > 2 * 1024 * 1024:
            raise ValueError("Camera frame exceeds 2 MiB.")
        part.extend(data[start:end])

    def part_end():
        nonlocal dimensions, accepted
        if accepted >= frame_count:
            return
        image_bytes = bytes(part)
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.format != "JPEG" or not (0 < image.width <= 1920 and 0 < image.height <= 1080):
                raise ValueError("Expected a JPEG frame no larger than 1920x1080.")
            if image.width % 2 or image.height % 2:
                raise ValueError("H.264 pilot requires even image dimensions.")
            image.load()
            if dimensions is not None and image.size != dimensions:
                raise ValueError("Camera dimensions changed during capture.")
            dimensions = image.size
        frames.append(image_bytes)
        accepted += 1

    parser = MultipartParser(boundary, {
        "on_part_begin": part_begin,
        "on_part_data": part_data,
        "on_part_end": part_end,
    })
    deadline = time.monotonic() + timeout_seconds
    received = 0
    while accepted < frame_count:
        if time.monotonic() >= deadline:
            raise TimeoutError("Camera capture deadline exceeded.")
        chunk = stream.read1(8192)
        if time.monotonic() >= deadline:
            raise TimeoutError("Camera capture deadline exceeded.")
        if not chunk:
            raise ValueError("Camera stream ended before enough complete frames arrived.")
        received += len(chunk)
        if received > max_bytes:
            raise ValueError("Camera capture exceeds the byte limit.")
        parser.write(chunk)
        while frames:
            yield frames.popleft()


def collect_frames(stream, content_type, frame_count, timeout_seconds=25, max_bytes=32 * 1024 * 1024):
    return list(iter_frames(stream, content_type, frame_count, timeout_seconds, max_bytes))


def realtime_frames(frames, fps, seconds, observed):
    if fps <= 0 or seconds <= 0:
        raise ValueError("Capture duration and FPS must be positive.")
    started = None
    previous = None
    emitted = 0
    observed.update(received_frames=0, capture_seconds=0.0)
    for frame in frames:
        now = time.monotonic()
        observed["received_frames"] += 1
        if started is None:
            started = now
            previous = frame
            continue
        elapsed = now - started
        observed["capture_seconds"] = round(elapsed, 3)
        while emitted / fps < min(elapsed, seconds):
            yield previous
            emitted += 1
        if elapsed >= seconds:
            return
        previous = frame
    raise ValueError("Camera stream ended before capture duration was reached.")


def media_tool(name):
    executable = shutil.which(name)
    if executable:
        return executable
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links" / f"{name}.exe"
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError(f"{name} is not installed or available in PATH.")


def encode_hls(frames, output_root, fps, encoder, frame_count=None):
    ffmpeg = media_tool("ffmpeg")
    ffprobe = media_tool("ffprobe")
    output_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output_root).free < 512 * 1024 * 1024:
        raise RuntimeError("At least 512 MiB of free space is required.")
    directory = output_root.resolve() / uuid.uuid4().hex
    directory.mkdir()
    playlist = directory / "index.m3u8"
    preset = "veryfast" if encoder == "libx264" else "p4"
    expected_frames = frame_count if frame_count is not None else len(frames)
    timeout = 30 + expected_frames / fps * 3
    arguments = [
        ffmpeg, "-hide_banner", "-nostdin", "-n", "-v", "error", "-xerror",
        "-probesize", "32768", "-analyzeduration", "0",
        "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "pipe:0",
        "-frames:v", str(expected_frames), "-an", "-c:v", encoder, "-preset", preset,
        "-tune", "zerolatency" if encoder == "libx264" else "ll",
        "-pix_fmt", "yuv420p", "-b:v", "2M", "-maxrate", "2M", "-bufsize", "4M",
        "-g", str(fps * 2), "-sc_threshold", "0",
        "-force_key_frames", "expr:gte(t,n_forced*2)",
        "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "event",
        "-hls_segment_type", "fmp4", "-hls_flags", "independent_segments+temp_file",
        "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", (directory / "segment-%03d.m4s").as_posix(),
        playlist.as_posix(),
    ]
    encoded_frames = 0
    dimensions = None
    published_before_eof = False
    with subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
        watchdog = threading.Timer(timeout, process.kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            for image_bytes in frames:
                if encoded_frames >= expected_frames:
                    raise RuntimeError("Too many input frames.")
                if dimensions is None:
                    with Image.open(io.BytesIO(image_bytes)) as image:
                        dimensions = image.size
                process.stdin.write(image_bytes)
                process.stdin.flush()
                encoded_frames += 1
                if encoded_frames < expected_frames and playlist.is_file():
                    published_before_eof = True
            process.stdin.close()
            if process.wait(timeout=timeout) != 0:
                raise RuntimeError("FFmpeg encoding failed or exceeded its deadline.")
            if encoded_frames != expected_frames:
                raise RuntimeError("Not enough input frames.")
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
    if "#EXT-X-ENDLIST" not in playlist.read_text(encoding="utf-8").splitlines():
        raise RuntimeError("The HLS playlist was not finalized.")
    probe_result = subprocess.run([
        ffprobe, "-v", "error", "-count_frames", "-show_streams", "-show_format",
        "-of", "json", playlist.as_posix(),
    ], capture_output=True, text=True, check=True, timeout=timeout)
    probe = json.loads(probe_result.stdout)
    video = [stream for stream in probe["streams"] if stream["codec_type"] == "video"]
    audio = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]
    duration = float(probe["format"]["duration"])
    if (
        len(video) != 1 or audio or video[0]["codec_name"] != "h264"
        or (video[0]["width"], video[0]["height"]) != dimensions
        or int(video[0]["nb_read_frames"]) != expected_frames
        or abs(duration - expected_frames / fps) > 1 / fps + 0.05
    ):
        raise RuntimeError("Unexpected HLS codec, dimensions, frame count, audio or duration.")
    subprocess.run([
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-xerror",
        "-i", playlist.as_posix(), "-map", "0:v:0", "-f", "null", "-",
    ], capture_output=True, check=True, timeout=timeout)
    segments = list(directory.glob("*.m4s"))
    if not segments or not (directory / "init.mp4").is_file():
        raise RuntimeError("Missing HLS segments or initialization file.")
    return {
        "playlist": str(playlist), "encoder": encoder, "frames": encoded_frames,
        "width": dimensions[0], "height": dimensions[1], "audio_tracks": len(audio),
        "playback_fps": fps, "playback_seconds": duration, "segments": len(segments),
        "size_bytes": sum(path.stat().st_size for path in directory.iterdir()),
        "endlist_verified": True, "decode_verified": True,
        "published_before_eof": published_before_eof,
    }


def validate_camera_url(url):
    address = urlsplit(url)
    if address.scheme not in ("http", "https") or not address.hostname or address.username is not None or address.password is not None or address.query or address.fragment:
        raise ValueError("Use an HTTP(S) camera URL without credentials, query or fragment.")


def camera_password(from_stdin=False):
    if from_stdin:
        if sys.stdin.isatty():
            raise RuntimeError("Password pipe must not be an interactive terminal.")
        encoded = sys.stdin.buffer.readline(4097)
        if not encoded.endswith(b"\n") or len(encoded) > 4096:
            raise RuntimeError("Invalid password pipe input.")
        password = encoded[:-1].removesuffix(b"\r").decode("utf-8")
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Camera password (hidden): ")
    if not password or "\n" in password or "\r" in password or len(password.encode("utf-8")) > 4095:
        raise RuntimeError("Camera password must be nonempty and fit on one line.")
    return password


def main():
    parser = argparse.ArgumentParser(description="Bounded local MJPEG streaming to HLS; no AI.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--replay", type=Path)
    parser.add_argument("--content-type", help="Required for replay: original multipart Content-Type header.")
    parser.add_argument("--pace-replay", action="store_true", help="Feed replay frames at the selected playback FPS.")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--seconds", type=int, default=10, help="Camera capture duration; replay playback duration at the selected FPS.")
    parser.add_argument("--fps", type=int, choices=(15, 30), default=30)
    parser.add_argument("--encoder", choices=("libx264", "h264_nvenc"), default="libx264")
    parser.add_argument("--output-root", type=Path, default=Path(__file__).resolve().parents[1] / "backend/data/phase0-pilots")
    args = parser.parse_args()
    if not 4 <= args.seconds <= 60:
        parser.error("--seconds must be between 4 and 60.")
    if args.replay and not args.content_type:
        parser.error("--content-type is required for replay.")
    if args.url:
        try:
            validate_camera_url(args.url)
        except ValueError:
            parser.error("Use an HTTP(S) camera URL without credentials, query or fragment.")
    media_tool("ffmpeg")
    media_tool("ffprobe")
    args.output_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.output_root).free < 512 * 1024 * 1024:
        raise RuntimeError("At least 512 MiB of free space is required.")
    target_frames = args.seconds * args.fps
    deadline_seconds = args.seconds * 2 + 5
    if args.replay:
        started = time.monotonic()
        with args.replay.open("rb") as source_file:
            frames = iter_frames(source_file, args.content_type, target_frames, deadline_seconds)
            if args.pace_replay:
                frames = paced_frames(frames, args.fps)
            result = encode_hls(frames, args.output_root, args.fps, args.encoder, target_frames)
        source_mode = "local-replay"
    else:
        print("Local capture only. HTTP Basic is unencrypted; use a trusted LAN.", flush=True)
        password = camera_password(args.password_stdin)
        with requests.Session() as session:
            session.trust_env = False
            session.auth = (args.username, password)
            del password
            with session.get(
                args.url, stream=True, timeout=(5, 3), allow_redirects=False,
                headers={"Accept-Encoding": "identity"},
            ) as response:
                if response.status_code in (401, 403):
                    raise CameraAuthenticationError("Camera authentication was refused.")
                if response.status_code != 200:
                    raise RuntimeError(f"Camera returned HTTP {response.status_code}.")
                started = time.monotonic()
                observed = {}
                frames = iter_frames(
                    response.raw, response.headers.get("Content-Type", ""),
                    args.seconds * 120 + 1, deadline_seconds, max_bytes=128 * 1024 * 1024,
                )
                frames = realtime_frames(frames, args.fps, args.seconds, observed)
                result = encode_hls(frames, args.output_root, args.fps, args.encoder, target_frames)
                result.update(observed, timing_mode="monotonic-receive")
        source_mode = "camera"
    elapsed_seconds = time.monotonic() - started
    result.update(source_mode=source_mode, elapsed_seconds=round(elapsed_seconds, 3))
    print(json.dumps(result, indent=2))


def paced_frames(frames, fps):
    started = time.monotonic()
    for index, frame in enumerate(frames):
        delay = started + index / fps - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        yield frame


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        if isinstance(error, RuntimeError):
            print(f"Pilot failed: {error}", file=sys.stderr)
        else:
            print(f"Pilot failed ({type(error).__name__}); no successful capture claimed.", file=sys.stderr)
        sys.exit(77 if isinstance(error, CameraAuthenticationError) else 1)