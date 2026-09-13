"""Measure real local verification through an isolated, synthetic API workload."""

import argparse
import asyncio
import base64
from datetime import datetime, timezone
from importlib.metadata import version
import io
import json
import logging
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time


async def measure_requests(app, payload, iterations, monitor, *, seconds=None):
    import httpx
    import psutil

    samples = []
    process = psutil.Process()
    batch_started = time.perf_counter()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://benchmark") as client:
        while (
            time.perf_counter() - batch_started < seconds
            if seconds is not None else len(samples) < iterations
        ):
            started = time.perf_counter()
            request = asyncio.create_task(client.post("/analyze-face", json=payload))
            health_latencies = []
            while not request.done():
                await asyncio.wait({request}, timeout=0.1)
                monitor.sample()
                if not request.done():
                    health_started = time.perf_counter()
                    health = await client.get("/health")
                    health.raise_for_status()
                    health_latencies.append(time.perf_counter() - health_started)
            response = await request
            response.raise_for_status()
            if len(response.json()["faces"]) != 1:
                raise RuntimeError("Expected one synthetic detection per request")
            samples.append({
                "iteration": len(samples) + 1,
                "seconds": round(time.perf_counter() - started, 4),
                "elapsed_seconds": round(time.perf_counter() - batch_started, 4),
                "rss_bytes": process.memory_info().rss,
                "health_checks_during_analysis": len(health_latencies),
                "health_max_seconds": round(max(health_latencies, default=0), 4),
                "status": response.status_code,
            })
    return samples


async def measure_with_video(app, payload, iterations, monitor, *, seconds, output_root):
    from PIL import Image, ImageDraw
    from scripts.ip_camera_pilot import encode_hls

    images = []
    for offset in range(0, 120, 8):
        image = Image.new("RGB", (1280, 720), "#243746")
        drawing = ImageDraw.Draw(image)
        for left in range(-120, 1280, 120):
            drawing.rectangle((left + offset, 0, left + offset + 60, 719), fill="#e0b432")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        images.append(buffer.getvalue())

    ready = threading.Event()
    stop = threading.Event()
    timing = {}
    fps = 15
    frame_count = int(seconds * fps)

    def frames():
        timing["started"] = time.perf_counter()
        ready.set()
        try:
            for index in range(frame_count):
                delay = timing["started"] + index / fps - time.perf_counter()
                if stop.wait(max(0, delay)):
                    raise RuntimeError("Synthetic video interrupted")
                yield images[index % len(images)]
            stop.wait(max(0, timing["started"] + seconds - time.perf_counter()))
        finally:
            timing["finished"] = time.perf_counter()

    def encode():
        try:
            return encode_hls(frames(), output_root, fps, "libx264", frame_count=frame_count)
        finally:
            ready.set()

    video_task = asyncio.create_task(asyncio.to_thread(encode))
    try:
        if not await asyncio.to_thread(ready.wait, 10):
            raise TimeoutError("Synthetic video did not start within ten seconds")
        if "started" not in timing:
            await video_task
            raise RuntimeError("Synthetic video produced no frames")
        analysis_started = time.perf_counter()
        samples = await measure_requests(app, payload, iterations, monitor, seconds=seconds)
        analysis_finished = time.perf_counter()
        video = await asyncio.shield(video_task)
        overlap = min(timing["finished"], analysis_finished) - max(timing["started"], analysis_started)
        if overlap <= 0:
            raise RuntimeError("Video encoding did not overlap recognition")
        video.pop("playlist", None)
        video.update(
            source="synthetic moving stripes; no camera",
            capture_seconds=round(timing["finished"] - timing["started"], 4),
            analysis_overlap_seconds=round(overlap, 4),
            media_retained=False,
        )
        return samples, video
    finally:
        stop.set()
        await asyncio.gather(video_task, return_exceptions=True)


def benchmark(iterations, *, seconds=None, with_video=False):
    import psutil
    from scripts.pilot_supervisor import ProcessMetrics

    if "main" in sys.modules or "config" in sys.modules:
        raise RuntimeError("Run this benchmark in its own Python process")
    if with_video and seconds is None:
        raise ValueError("Concurrent video requires seconds per gallery")
    resources = {}
    process = psutil.Process()
    initial_cpu = process.cpu_times()
    monitor = ProcessMetrics(os.getpid(), resources)
    with tempfile.TemporaryDirectory(prefix="recognition_benchmark_") as temporary:
        root = Path(temporary)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        from config import Settings

        Settings.DATA_DIR = str(root)
        Settings.DB_PATH = str(root / "benchmark.db")
        Settings.REFERENCES_DIR = str(root / "references")
        Settings.AZURE_FACE_ENDPOINT = ""
        Settings.AZURE_FACE_KEY = ""
        Settings.API_KEY = ""
        Settings.AUTO_ENROLL = False
        Settings.RECOGNITION_ACTION = "none"
        Settings.DEEPFACE_MODEL = "VGG-Face"

        import_started = time.perf_counter()
        import numpy as np
        from PIL import Image
        import database
        import main
        import recognition

        import_seconds = time.perf_counter() - import_started
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("recognition.actions").setLevel(logging.WARNING)
        if recognition.DeepFace is None:
            raise RuntimeError("Install the backend ML runtime before benchmarking")
        recognition.detect_faces = lambda image: [{
            "faceRectangle": {"top": 0, "left": 0, "width": 224, "height": 224},
            "faceAttributes": {"headPose": {}},
        }]
        recognition.analyze_demographics = lambda image: None
        database.init_db()
        generator = np.random.default_rng(42)
        payload = {}
        batches = []
        for reference_count in (1, 3):
            for index in range(len(database.list_references()), reference_count):
                path = root / "references" / f"synthetic-{index}.jpg"
                pixels = generator.integers(0, 256, (224, 224, 3), dtype=np.uint8)
                Image.fromarray(pixels).save(path)
                database.add_reference(f"Synthetic {index}", str(path))
                if index == 0:
                    payload = {"image": base64.b64encode(path.read_bytes()).decode("ascii")}
            if with_video:
                samples, video = asyncio.run(measure_with_video(
                    main.app, payload, iterations, monitor, seconds=seconds,
                    output_root=root / "video",
                ))
                batches.append({"reference_count": reference_count, "samples": samples, "video": video})
            else:
                samples = asyncio.run(measure_requests(main.app, payload, iterations, monitor, seconds=seconds))
                batches.append({"reference_count": reference_count, "samples": samples})
        if len(database.list_references()) != 3 or list(root.glob("capture_*.jpg")):
            raise RuntimeError("Unexpected enrollment or temporary capture leak")
        monitor.sample()
        resources["cpu_seconds_observed"] = round(
            resources["cpu_seconds_observed"] - initial_cpu.user - initial_cpu.system, 3,
        )
        resources["cpu_percent_one_core"] = round(
            100 * resources["cpu_seconds_observed"] / resources["elapsed_seconds"], 2,
        )
        warm_samples = [sample["seconds"] for batch in batches for sample in batch["samples"]][1:]
        return {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": "In-process ASGI, synthetic Azure detection, demographics disabled, real DeepFace verification",
            "model": Settings.DEEPFACE_MODEL,
            "deepface_version": version("deepface"),
            "opencv_version": version("opencv-python"),
            "python_version": sys.version.split()[0],
            "logical_cpus": psutil.cpu_count(),
            "tensorflow_gpus": len(__import__("tensorflow").config.list_physical_devices("GPU")),
            "detector_backend": "opencv (DeepFace default)",
            "seconds_per_gallery": seconds,
            "with_video": with_video,
            "resource_scope": "benchmark process and observed child processes",
            "import_seconds": round(import_seconds, 4),
            "first_request_seconds": batches[0]["samples"][0]["seconds"],
            "warm_median_seconds": round(statistics.median(warm_samples), 4),
            "resources": resources,
            "batches": batches,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    workload = parser.add_mutually_exclusive_group()
    workload.add_argument("--iterations", type=int, choices=range(1, 21), default=3)
    workload.add_argument(
        "--seconds-per-gallery", type=int, choices=range(1, 901), metavar="1..900",
        help="Timed workload for each gallery; finishes the request in progress at the deadline",
    )
    parser.add_argument("--output", type=Path, help="New JSON report path; existing files are never overwritten")
    parser.add_argument("--with-video", action="store_true", help="Encode temporary synthetic 720p15 HLS during each timed gallery")
    args = parser.parse_args()
    if args.with_video and args.seconds_per_gallery is None:
        parser.error("--with-video requires --seconds-per-gallery")
    if args.output and (args.output.exists() or not args.output.parent.is_dir()):
        parser.error("Report must be a new file in an existing directory")
    report = benchmark(args.iterations, seconds=args.seconds_per_gallery, with_video=args.with_video)
    encoded = json.dumps(report, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as destination:
            destination.write(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()