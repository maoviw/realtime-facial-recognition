import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from scripts import benchmark_recognition


@pytest.mark.parametrize("seconds, expected", [(None, 2), (2, 2), (2.5, 3)])
def test_workload_stops_between_requests(monkeypatch, seconds, expected):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(benchmark_recognition, "time", SimpleNamespace(perf_counter=lambda: clock.now))
    app = FastAPI()

    @app.post("/analyze-face")
    async def analyze():
        clock.now += 1
        return {"faces": [{}]}

    samples = asyncio.run(benchmark_recognition.measure_requests(
        app, {}, 2, SimpleNamespace(sample=lambda: None), seconds=seconds,
    ))
    assert len(samples) == expected
    assert [sample["iteration"] for sample in samples] == list(range(1, expected + 1))
    assert samples[-1]["elapsed_seconds"] == expected
    assert all(sample["seconds"] == 1 and sample["rss_bytes"] > 0 for sample in samples)
    assert all(sample["status"] == 200 for sample in samples)


@pytest.mark.parametrize("arguments", [
    ["--seconds-per-gallery", "0"],
    ["--seconds-per-gallery", "901"],
    ["--seconds-per-gallery", "1", "--iterations", "2"],
    ["--with-video"],
])
def test_cli_rejects_invalid_duration(monkeypatch, arguments):
    monkeypatch.setattr(benchmark_recognition.sys, "argv", ["benchmark", *arguments])
    with pytest.raises(SystemExit) as error:
        benchmark_recognition.main()
    assert error.value.code == 2


def test_cli_writes_timed_report_without_overwriting(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    monkeypatch.setattr(benchmark_recognition.sys, "argv", [
        "benchmark", "--seconds-per-gallery", "300", "--output", str(output),
    ])
    calls = []

    def measure(iterations, *, seconds, with_video):
        assert not with_video
        calls.append((iterations, seconds))
        return {"seconds_per_gallery": seconds}

    monkeypatch.setattr(benchmark_recognition, "benchmark", measure)
    benchmark_recognition.main()
    original = output.read_bytes()
    with pytest.raises(SystemExit) as error:
        benchmark_recognition.main()
    assert error.value.code == 2
    assert calls == [(3, 300)]
    assert output.read_bytes() == original


@pytest.mark.parametrize("failure", [None, "analysis", "encoding"])
def test_concurrent_video_cleanup(tmp_path, monkeypatch, failure):
    from scripts import ip_camera_pilot

    state = {"active": False, "finished": False}

    def encode(frames, output_root, fps, encoder, frame_count):
        state["active"] = True
        try:
            if failure == "encoding":
                raise RuntimeError("encoder failure")
            assert len(list(frames)) == frame_count == 3
            return {"playlist": "temporary/index.m3u8", "decode_verified": True}
        finally:
            state["active"] = False
            state["finished"] = True

    async def measure(*args, **kwargs):
        assert state["active"]
        if failure == "analysis":
            raise RuntimeError("analysis failure")
        await asyncio.sleep(0.1)
        return [{"status": 200}]

    monkeypatch.setattr(ip_camera_pilot, "encode_hls", encode)
    monkeypatch.setattr(benchmark_recognition, "measure_requests", measure)
    workload = benchmark_recognition.measure_with_video(
        None, {}, 1, None, seconds=0.2, output_root=tmp_path,
    )
    if failure:
        with pytest.raises(RuntimeError, match="failure"):
            asyncio.run(workload)
    else:
        samples, video = asyncio.run(workload)
        assert samples == [{"status": 200}]
        assert 0 < video["analysis_overlap_seconds"] <= 0.2
        assert video["decode_verified"]
        assert not video["media_retained"]
        assert "playlist" not in video
    assert state == {"active": False, "finished": True}


def test_concurrent_video_real_encoding(tmp_path, monkeypatch):
    from scripts import ip_camera_pilot

    try:
        ip_camera_pilot.media_tool("ffmpeg")
        ip_camera_pilot.media_tool("ffprobe")
    except RuntimeError:
        pytest.skip("FFmpeg and FFprobe required for concurrent video integration")

    async def measure(*args, **kwargs):
        await asyncio.sleep(4)
        return [{"status": 200}]

    monkeypatch.setattr(benchmark_recognition, "measure_requests", measure)
    samples, video = asyncio.run(benchmark_recognition.measure_with_video(
        None, {}, 1, None, seconds=4, output_root=tmp_path,
    ))
    assert samples == [{"status": 200}]
    assert video["frames"] == 60
    assert (video["width"], video["height"]) == (1280, 720)
    assert video["analysis_overlap_seconds"] >= 3.5
    assert video["playback_seconds"] == pytest.approx(4, abs=0.1)
    assert video["endlist_verified"] and video["decode_verified"]
    assert video["published_before_eof"]