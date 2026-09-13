import asyncio
import base64
import io
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import httpx
import pytest
import requests
from requests.auth import HTTPBasicAuth

import database
import main
import recognition
from config import settings

_detect_faces = recognition.detect_faces
_verify_against_references = recognition.verify_against_references

# Un pixel JPEG encodé en base64 (data URL).
PIXEL_B64 = (
    "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
    "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAA"
    "AAAACP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AfwD/2Q=="
)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["azure_configured"] is True


def test_settings(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "headpose_pitch_max" in resp.json()


def test_analyze_no_face(client, monkeypatch):
    monkeypatch.setattr(recognition, "detect_faces", lambda b: [])
    resp = client.post("/analyze-face", json={"image": PIXEL_B64})
    assert resp.status_code == 200
    assert resp.json() == {"faces": []}


@pytest.mark.parametrize("engine_failure", [False, True])
def test_analysis_keeps_api_responsive_and_rejects_overlap(client, monkeypatch, engine_failure):
    started = Event()
    release = Event()
    finished = Event()

    def blocked_detection(image):
        started.set()
        release.wait(5)
        finished.set()
        if engine_failure:
            raise recognition.EngineUnavailableError("Moteur de test indisponible")
        return []

    monkeypatch.setattr(recognition, "detect_faces", blocked_detection)

    async def exercise():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            analysis = asyncio.create_task(async_client.post("/analyze-face", json={"image": PIXEL_B64}))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                health = await asyncio.wait_for(async_client.get("/health"), timeout=1)
                assert health.status_code == 200
                assert not finished.is_set(), "Analysis blocked the API event loop"
                history = await asyncio.wait_for(async_client.get("/history"), timeout=1)
                assert history.json() == {"events": []}
                overlap = await asyncio.wait_for(
                    async_client.post("/analyze-face", json={"image": PIXEL_B64}), timeout=1,
                )
                assert overlap.status_code == 429
                assert overlap.headers["retry-after"] == "1"
            finally:
                release.set()
                response = await analysis
            assert response.status_code == (503 if engine_failure else 200)
            monkeypatch.setattr(recognition, "detect_faces", lambda image: [])
            retry = await async_client.post("/analyze-face", json={"image": PIXEL_B64})
            assert retry.status_code == 200

    asyncio.run(exercise())


def test_cancelled_client_does_not_release_running_analysis(client, monkeypatch):
    started = Event()
    release = Event()

    def blocked_detection(image):
        started.set()
        release.wait(5)
        return []

    monkeypatch.setattr(recognition, "detect_faces", blocked_detection)

    async def exercise():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            analysis = asyncio.create_task(async_client.post("/analyze-face", json={"image": PIXEL_B64}))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                analysis.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await analysis
                overlap = await async_client.post("/analyze-face", json={"image": PIXEL_B64})
                assert overlap.status_code == 429
            finally:
                release.set()
                acquired = await asyncio.to_thread(main._analysis_lock.acquire, True, 2)
                if acquired:
                    main._analysis_lock.release()
                assert acquired, "Worker did not release its analysis slot"
            retry = await async_client.post("/analyze-face", json={"image": PIXEL_B64})
            assert retry.status_code == 200

    asyncio.run(exercise())


@pytest.mark.parametrize("configured", [False, True])
def test_detection_failure_is_not_an_empty_result(client, monkeypatch, configured):
    service = Mock()
    service.analyze_face_quality.side_effect = RuntimeError("private-provider-details")
    monkeypatch.setattr(recognition, "azure_service", service if configured else None)
    monkeypatch.setattr(recognition, "detect_faces", _detect_faces)
    response = client.post("/analyze-face", json={"image": PIXEL_B64})
    assert response.status_code == (502 if configured else 503)
    assert "private-provider-details" not in response.text
    assert client.get("/history").json() == {"events": []}
    assert database.list_references() == []


@pytest.mark.parametrize("failure", ["missing-engine", "missing-image", "model-error"])
def test_verification_failure_never_enrolls(client, monkeypatch, tmp_path, failure):
    fake = [{"faceRectangle": {"top": 0, "left": 0, "width": 1, "height": 1},
             "faceAttributes": {"headPose": {}}}]
    reference_path = tmp_path / "reference.jpg"
    if failure != "missing-image":
        reference_path.write_bytes(base64.b64decode(PIXEL_B64.split(",", 1)[1]))
    database.add_reference("Reference", str(reference_path))
    engine = Mock()
    engine.verify.side_effect = RuntimeError("private-model-details")
    monkeypatch.setattr(recognition, "DeepFace", None if failure == "missing-engine" else engine)
    monkeypatch.setattr(recognition, "detect_faces", lambda image: fake)
    monkeypatch.setattr(recognition, "verify_against_references", _verify_against_references)
    response = client.post("/analyze-face", json={"image": PIXEL_B64})
    assert response.status_code == 503
    assert "private-model-details" not in response.text
    assert len(database.list_references()) == 1
    assert client.get("/history").json() == {"events": []}
    assert not list(tmp_path.glob("capture_*.jpg"))


def test_analyze_recognizes_despite_head_pose(client, monkeypatch):
    fake = [{"faceRectangle": {"top": 1, "left": 2, "width": 3, "height": 4},
             "faceAttributes": {"headPose": {"pitch": 40, "yaw": 0, "roll": 0}}}]
    monkeypatch.setattr(recognition, "detect_faces", lambda b: fake)
    verify = Mock(return_value=(True, 0.91, {"id": 1, "name": "Alice"}))
    monkeypatch.setattr(recognition, "verify_against_references", verify)
    resp = client.post("/analyze-face", json={"image": PIXEL_B64})
    assert resp.status_code == 200
    face = resp.json()["faces"][0]
    assert face["recognized"] is True
    assert face["name"] == "Alice"
    assert face["pitch"] == 40
    verify.assert_called_once()
    assert not Path(verify.call_args.args[0]).exists()


def test_analyze_recognized(client, monkeypatch):
    fake = [{"faceRectangle": {"top": 1, "left": 2, "width": 3, "height": 4},
             "faceAttributes": {"headPose": {"pitch": 0, "yaw": 0, "roll": 0}}}]
    monkeypatch.setattr(recognition, "detect_faces", lambda b: fake)
    monkeypatch.setattr(
        recognition, "verify_against_references",
        lambda path: (True, 0.91, {"id": 1, "name": "Alice"}),
    )
    resp = client.post("/analyze-face", json={"image": PIXEL_B64})
    assert resp.status_code == 200
    face = resp.json()["faces"][0]
    assert face["recognized"] is True
    assert face["name"] == "Alice"
    assert face["confidence"] == 0.91


def test_auto_enroll_unknown_clear_face(client, monkeypatch):
    """Un visage net non reconnu est auto-enrôlé puis renvoyé comme « Visage N »."""
    fake = [{"faceRectangle": {"top": 0, "left": 0, "width": 1, "height": 1},
             "faceAttributes": {"headPose": {"pitch": 0, "yaw": 0, "roll": 0}}}]
    monkeypatch.setattr(recognition, "detect_faces", lambda b: fake)
    # DeepFace présent (sentinel) mais aucune correspondance -> auto-enrôlement.
    monkeypatch.setattr(recognition, "DeepFace", object())
    monkeypatch.setattr(recognition, "verify_against_references", lambda p: (False, 0.0, None))

    resp = client.post("/analyze-face", json={"image": PIXEL_B64})
    assert resp.status_code == 200
    face = resp.json()["faces"][0]
    assert face["recognized"] is False
    assert face["confidence"] == 0.0
    assert face["name"].startswith("Visage ")
    assert face["system_action"] == "Auto-enrôlé"

    refs = client.get("/references").json()["references"]
    assert any(r["name"].startswith("Visage ") and r["auto"] is True for r in refs)
    event = client.get("/history").json()["events"][0]
    assert event["recognized"] is False
    assert event["confidence"] == 0.0


def test_auto_enroll_disabled(client, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "AUTO_ENROLL", False)
    fake = [{"faceRectangle": {"top": 0, "left": 0, "width": 1, "height": 1},
             "faceAttributes": {"headPose": {"pitch": 0, "yaw": 0, "roll": 0}}}]
    monkeypatch.setattr(recognition, "detect_faces", lambda b: fake)
    monkeypatch.setattr(recognition, "verify_against_references", lambda p: (False, 0.0, None))
    resp = client.post("/analyze-face", json={"image": PIXEL_B64})
    assert resp.json()["faces"][0]["recognized"] is False


def test_rename_reference(client):
    img = base64.b64decode(PIXEL_B64.split(",", 1)[1])
    ref_id = client.post(
        "/references",
        data={"name": "Visage 1"},
        files={"file": ("v.jpg", io.BytesIO(img), "image/jpeg")},
    ).json()["id"]

    resp = client.patch(f"/references/{ref_id}", json={"name": "Charlie"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Charlie"

    listed = client.get("/references").json()["references"]
    assert any(r["id"] == ref_id and r["name"] == "Charlie" for r in listed)

    # Renommer une référence inexistante -> 404.
    assert client.patch("/references/999999", json={"name": "X"}).status_code == 404


def test_reference_image_endpoint(client):
    img = base64.b64decode(PIXEL_B64.split(",", 1)[1])
    ref_id = client.post(
        "/references",
        data={"name": "Dora"},
        files={"file": ("d.jpg", io.BytesIO(img), "image/jpeg")},
    ).json()["id"]
    resp = client.get(f"/references/{ref_id}/image")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")
    assert client.get("/references/999999/image").status_code == 404


def test_enroll_and_list_and_delete(client):
    img = base64.b64decode(PIXEL_B64.split(",", 1)[1])
    resp = client.post(
        "/references",
        data={"name": "Bob"},
        files={"file": ("bob.jpg", io.BytesIO(img), "image/jpeg")},
    )
    assert resp.status_code == 200
    ref_id = resp.json()["id"]

    listed = client.get("/references").json()["references"]
    assert any(r["id"] == ref_id for r in listed)
    # image_path (chemin filesystem interne) ne doit jamais fuiter via l'API.
    assert all("image_path" not in r for r in listed)

    deleted = client.delete(f"/references/{ref_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == ref_id


def test_enroll_rejects_non_image(client):
    resp = client.post(
        "/references",
        data={"name": "Mallory"},
        files={"file": ("evil.jpg", io.BytesIO(b"not an image"), "image/jpeg")},
    )
    assert resp.status_code == 400


def test_analyze_rejects_non_image(client):
    # base64 valide mais contenu non-image -> rejet de format.
    payload = "data:image/jpeg;base64," + base64.b64encode(b"hello world").decode()
    resp = client.post("/analyze-face", json={"image": payload})
    assert resp.status_code == 400


def test_history_limit_is_bounded(client, monkeypatch):
    monkeypatch.setattr(settings, "HISTORY_LIMIT_MAX", 3)
    monkeypatch.setattr(database, "_now", lambda: "2026-09-12T12:00:00+00:00")
    for index in range(5):
        database.log_event(False, 0.0, name=f"event-{index}")
    resp = client.get("/history?limit=999999")
    assert resp.status_code == 200
    assert [event["name"] for event in resp.json()["events"]] == [
        "event-4", "event-3", "event-2",
    ]
    assert len(client.get("/history?limit=0").json()["events"]) == 1


def test_history_records_events(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_ENROLL", False)
    fake = [{"faceRectangle": {"top": 0, "left": 0, "width": 1, "height": 1},
             "faceAttributes": {"headPose": {"pitch": 2, "yaw": 3, "roll": 4}}}]
    monkeypatch.setattr(recognition, "detect_faces", lambda image: fake)
    analysis = client.post("/analyze-face", json={"image": PIXEL_B64})
    assert analysis.status_code == 200
    resp = client.get("/history")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 1
    assert events[0]["recognized"] is False
    assert events[0]["confidence"] == 0.0
    assert events[0]["pitch"] == 2
    assert events[0]["yaw"] == 3
    assert events[0]["roll"] == 4
    assert events[0]["system_action"] == analysis.json()["faces"][0]["system_action"]


def test_no_face_does_not_create_history(client, monkeypatch):
    monkeypatch.setattr(recognition, "detect_faces", lambda b: [])
    assert client.post("/analyze-face", json={"image": PIXEL_B64}).status_code == 200
    assert client.get("/history").json() == {"events": []}


@pytest.mark.parametrize("iteration", [1, 2])
def test_storage_starts_empty(client, iteration):
    assert client.get("/references").json() == {"references": []}
    assert client.get("/history").json() == {"events": []}
    assert database.next_auto_label() == "Visage 1"
    database.add_reference(f"probe-{iteration}", str(Path(settings.REFERENCES_DIR) / "probe.jpg"))
    database.log_event(False, 0.0)


def test_bad_base64(client, monkeypatch):
    fake = [{"faceRectangle": {}, "faceAttributes": {"headPose": {}}}]
    monkeypatch.setattr(recognition, "detect_faces", lambda b: fake)
    resp = client.post("/analyze-face", json={"image": "!!!notbase64!!!"})
    assert resp.status_code == 400


@pytest.mark.parametrize("multipart", [False, True])
def test_proxy_camera_returns_jpeg_without_analysis(client, monkeypatch, multipart):
    image_bytes = base64.b64decode(PIXEL_B64.split(",", 1)[1])
    camera_response = Mock()
    camera_response.headers = {
        "Content-Type": "multipart/x-mixed-replace; boundary=frame" if multipart else "image/jpeg",
    }
    camera_response.content = image_bytes
    camera_response.iter_content.return_value = iter([
        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + image_bytes[:1],
        image_bytes[1:-1],
        image_bytes[-1:] + b"\r\n--frame\r\n",
    ])
    camera_get = Mock(return_value=camera_response)
    monkeypatch.setattr(requests, "get", camera_get)
    response = client.post("/proxy-camera", json={
        "url": "http://camera.test/image/jpeg.cgi",
        "username": "test-user",
        "password": "test-password",
    })
    assert response.status_code == 200
    assert response.json()["image"] == PIXEL_B64
    camera_get.assert_called_once_with(
        "http://camera.test/image/jpeg.cgi",
        auth=HTTPBasicAuth("test-user", "test-password"), timeout=5, stream=True,
    )
    recognition.detect_faces.assert_not_called()
    recognition.verify_against_references.assert_not_called()
    assert database.list_references() == []
    assert client.get("/history").json() == {"events": []}


@pytest.mark.parametrize("content_type,content,status", [
    ("text/html", b"<html>Login</html>", 400),
    ("image/jpeg", b"", 502),
])
def test_proxy_camera_rejects_html_and_empty_image(client, monkeypatch, content_type, content, status):
    camera_response = Mock(headers={"Content-Type": content_type}, content=content)
    monkeypatch.setattr(requests, "get", Mock(return_value=camera_response))
    response = client.post("/proxy-camera", json={"url": "http://camera.test/image/jpeg.cgi"})
    assert response.status_code == status
    assert "image" not in response.json()


@pytest.mark.parametrize("failure", ["unauthorized", "timeout"])
def test_proxy_camera_reports_network_failure(client, monkeypatch, failure):
    camera_response = Mock()
    camera_get = Mock(return_value=camera_response)
    if failure == "unauthorized":
        camera_response.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
    else:
        camera_get.side_effect = requests.Timeout("Camera timed out")
    monkeypatch.setattr(requests, "get", camera_get)
    response = client.post("/proxy-camera", json={"url": "http://camera.test/image/jpeg.cgi"})
    assert response.status_code == 502
    assert "image" not in response.json()


def test_proxy_camera_requires_configured_api_key(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "test-api-key")
    camera_get = Mock()
    monkeypatch.setattr(requests, "get", camera_get)
    response = client.post("/proxy-camera", json={"url": "http://camera.test/image/jpeg.cgi"})
    assert response.status_code == 401
    camera_get.assert_not_called()
