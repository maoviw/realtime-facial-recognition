import asyncio
import base64
import io
from pathlib import Path
from threading import Event
from unittest.mock import Mock
import logging

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

    # Un nom uniquement composé d'espaces est rejeté (422) après strip.
    assert client.patch(f"/references/{ref_id}", json={"name": "   "}).status_code == 422


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


def test_search_history_matches_name_and_track(client):
    database.log_event(
        recognized=True,
        confidence=0.9,
        name="Alice",
        event_type="face",
        track_id="face-7",
        summary="Personne reconnue",
    )
    response = client.get("/history/search?q=Alice")
    assert response.status_code == 200
    event = response.json()["events"][0]
    assert event["name"] == "Alice"
    assert event["track_id"] == "face-7"
    assert event["objects"] == []


def test_zone_and_alert_rule_lifecycle(client):
    zone = client.post(
        "/zones",
        json={"name": "Entrée", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    )
    assert zone.status_code == 200
    zone_id = zone.json()["id"]
    rule = client.post(
        "/alerts",
        json={"name": "Visage dans l'entrée", "zone_id": zone_id, "cooldown_seconds": 30},
    )
    assert rule.status_code == 200
    assert rule.json()["zone_id"] == zone_id
    assert client.delete(f"/alerts/{rule.json()['id']}").status_code == 200
    assert client.delete(f"/zones/{zone_id}").status_code == 200


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
# --- Sprint 1 — Sécurité & conformité ---

def test_security_headers_present(client):
    resp = client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "content-security-policy" in resp.headers


def test_requires_api_key(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "API_KEY", "secret")
    # Sans en-tête -> 401.
    assert client.get("/references").status_code == 401
    # Avec la bonne clé -> 200.
    assert client.get("/references", headers={"x-api-key": "secret"}).status_code == 200


def test_pii_masking(client, monkeypatch, caplog):
    from config import settings

    monkeypatch.setattr(settings, "LOG_MASK_PII", True)
    fake = [{"faceRectangle": {"top": 1, "left": 2, "width": 3, "height": 4},
             "faceAttributes": {"headPose": {"pitch": 0, "yaw": 0, "roll": 0}}}]
    monkeypatch.setattr(recognition, "detect_faces", lambda b: fake)
    monkeypatch.setattr(
        recognition, "verify_against_references",
        lambda path: (True, 0.91, {"id": 1, "name": "Alice"}),
    )
    with caplog.at_level(logging.INFO):
        client.post("/analyze-face", json={"image": PIXEL_B64})
    # Le nom en clair ne doit jamais apparaître dans les logs ; sa forme
    # masquée (première lettre + astérisques) oui.
    assert "Alice" not in caplog.text
    assert "A****" in caplog.text


def test_rate_limit_enforced():
    """Le câblage slowapi renvoie bien un 429 au-delà de la limite."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from slowapi import Limiter
    from slowapi.middleware import SlowAPIMiddleware
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address

    app = FastAPI()
    app.state.limiter = Limiter(key_func=get_remote_address, default_limits=["3/minute"])
    app.add_exception_handler(RateLimitExceeded, main._rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    with TestClient(app) as c:
        statuses = [c.get("/ping").status_code for _ in range(6)]
    assert 429 in statuses


# --- Sprint 2 — Fiabilité & résilience ---

def test_azure_retries_then_succeeds(monkeypatch):
    """detect_faces réessaie sur erreur réseau transitoire puis réussit."""
    from config import settings

    monkeypatch.setattr(settings, "AZURE_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "AZURE_BACKOFF_BASE", 0)  # pas d'attente en test
    monkeypatch.setattr(recognition.time, "sleep", lambda s: None)

    calls = {"n": 0}

    service = Mock()
    def fake_detect(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return []

    service.analyze_face_quality.side_effect = fake_detect
    monkeypatch.setattr(recognition, "azure_service", service)
    result = _detect_faces(b"img")
    assert calls["n"] == 3
    assert result == []


def test_azure_retries_exhausted(monkeypatch):
    """Après épuisement des tentatives, l'erreur réseau est propagée."""
    from config import settings

    monkeypatch.setattr(settings, "AZURE_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "AZURE_BACKOFF_BASE", 0)
    monkeypatch.setattr(recognition.time, "sleep", lambda s: None)

    service = Mock()
    def always_fail(*a, **k):
        raise RuntimeError("slow")

    service.analyze_face_quality.side_effect = always_fail
    monkeypatch.setattr(recognition, "azure_service", service)
    with pytest.raises(recognition.DetectionError):
        _detect_faces(b"img")


def test_deepface_timeout_skips_reference(monkeypatch, tmp_path):
    """Une vérification DeepFace qui dépasse le délai n'interrompt pas la boucle."""
    import time as _time

    from config import settings

    import types

    ref_img = tmp_path / "ref.jpg"
    ref_img.write_bytes(b"x")
    monkeypatch.setattr(settings, "DEEPFACE_TIMEOUT", 0.2)

    def slow_verify(*a, **k):
        _time.sleep(2)
        return {"verified": True, "distance": 0.1}

    monkeypatch.setattr(recognition, "DeepFace", types.SimpleNamespace(verify=slow_verify))
    monkeypatch.setattr(
        recognition, "list_references_internal",
        lambda: [{"id": 1, "name": "Slow", "image_path": str(ref_img)}],
    )
    recognized, confidence, matched = recognition.verify_against_references("cap.jpg")
    assert recognized is False
    assert matched is None


def test_purge_old_events(client):
    import database

    # Événement ancien (au-delà du TTL) + événement récent.
    with database.get_connection() as conn:
        conn.execute(
            """INSERT INTO recognition_events
               (recognized, confidence, system_action, created_at)
               VALUES (0, 0.0, 'old', '2000-01-01T00:00:00+00:00')"""
        )
    database.log_event(recognized=False, confidence=0.0, system_action="new")

    removed = database.purge_old_events(ttl_days=30, max_rows=0)
    assert removed >= 1
    remaining = [e["system_action"] for e in database.list_events(limit=100)]
    assert "old" not in remaining
    assert "new" in remaining


def test_purge_max_rows_cap(client):
    import database

    for i in range(5):
        database.log_event(recognized=False, confidence=0.0, system_action=f"e{i}")
    database.purge_old_events(ttl_days=0, max_rows=3)
    assert len(database.list_events(limit=100)) <= 3


def test_metrics_endpoint_exposes_counters(client):
    # Génère un peu de trafic d'abord.
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "http_requests_total" in body
    assert "# TYPE http_requests_total counter" in body
    assert "azure_requests_total" in body


def test_metrics_count_increases(client):
    import metrics

    before = metrics.snapshot()["http_requests_total"]
    client.get("/health")
    after = metrics.snapshot()["http_requests_total"]
    assert after > before


def test_json_log_formatter_outputs_json():
    import json
    import logging

    import main

    rec = logging.LogRecord(
        name="recognition.api", level=logging.INFO, pathname=__file__,
        lineno=1, msg="hello %s", args=("world",), exc_info=None,
    )
    line = main._JsonLogFormatter().format(rec)
    parsed = json.loads(line)
    assert parsed["level"] == "INFO"
    assert parsed["msg"] == "hello world"
    assert parsed["logger"] == "recognition.api"


def test_delete_reference_nulls_event_link(client, monkeypatch):
    import database

    fake = [{"faceRectangle": {"top": 1, "left": 2, "width": 3, "height": 4},
             "faceAttributes": {"headPose": {"pitch": 0, "yaw": 0, "roll": 0}}}]
    monkeypatch.setattr(recognition, "detect_faces", lambda b: fake)
    monkeypatch.setattr(
        recognition, "verify_against_references",
        lambda path: (True, 0.91, {"id": 1, "name": "Alice"}),
    )
    # Crée une vraie référence puis un événement la référençant.
    img = base64.b64decode(PIXEL_B64.split(",", 1)[1])
    ref_id = client.post(
        "/references",
        data={"name": "Eve"},
        files={"file": ("e.jpg", io.BytesIO(img), "image/jpeg")},
    ).json()["id"]
    database.log_event(recognized=True, confidence=0.9, reference_id=ref_id, name="Eve")

    assert client.delete(f"/references/{ref_id}").status_code == 200
    # Aucun événement ne doit encore pointer vers la référence supprimée.
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM recognition_events WHERE reference_id = ?",
            (ref_id,),
        ).fetchone()
    assert rows["n"] == 0
