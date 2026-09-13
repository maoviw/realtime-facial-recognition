"""API FastAPI de reconnaissance faciale temps réel.

Endpoints :
  GET  /health              -> état du service
  POST /analyze-face        -> analyse une image (multi-visages, multi-références)
  POST /references          -> enrôle un visage de référence (upload)
  GET  /references          -> liste les références
  DELETE /references/{id}   -> supprime une référence
  GET  /history             -> historique des reconnaissances
  GET  /settings            -> seuils et configuration courante
"""
import asyncio
import base64
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from threading import Lock

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

import database
import metrics
import recognition
from tracking import FaceTracker
import zones as zone_logic
from actions import trigger_action, trigger_alert
from config import mask_name, settings


class _JsonLogFormatter(logging.Formatter):
    """Formate chaque enregistrement de log en une ligne JSON (ingestion)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    if settings.LOG_FORMAT == "json":
        handler.setFormatter(_JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))


_configure_logging()
logger = logging.getLogger("recognition.api")
_analysis_lock = Lock()
_face_tracker = FaceTracker()


# Signatures binaires des formats image acceptés (magic bytes).
_IMAGE_MAGIC = (
    b"\xff\xd8\xff",          # JPEG
    b"\x89PNG\r\n\x1a\n",     # PNG
    b"RIFF",                  # WEBP (RIFF....WEBP)
    b"BM",                    # BMP
)


def _looks_like_image(data: bytes) -> bool:
    if data.startswith(b"RIFF"):
        return data[8:12] == b"WEBP"
    return any(data.startswith(sig) for sig in _IMAGE_MAGIC)


async def _periodic_purge(interval_seconds: float = 6 * 3600) -> None:
    """Tâche d'arrière-plan : purge périodiquement l'historique (R10)."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            removed = await asyncio.to_thread(database.purge_old_events)
            if removed:
                logger.info("Purge historique : %d événement(s) supprimé(s).", removed)
        except Exception as exc:  # noqa: BLE001
            logger.error("Échec de la purge d'historique : %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.is_production() and not settings.API_KEY:
        raise RuntimeError(
            "API_KEY est obligatoire en production (ENV=production). "
            "Définissez API_KEY ou passez en ENV=development."
        )
    database.init_db()
    if not settings.azure_configured():
        logger.warning("Clés Azure manquantes : la détection de visages échouera.")
    if recognition.DeepFace is None:
        logger.warning("DeepFace non installé : la vérification 1:1 échouera (pip install deepface).")
    # Purge au démarrage, puis périodiquement en arrière-plan.
    removed = database.purge_old_events()
    if removed:
        logger.info("Purge historique au démarrage : %d événement(s) supprimé(s).", removed)
    purge_task = asyncio.create_task(_periodic_purge())
    logger.info("Démarrage OK — %d référence(s) enrôlée(s).", len(database.list_references()))
    try:
        yield
    finally:
        purge_task.cancel()
        try:
            await purge_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Real-time Facial Recognition API", version="2.0.0", lifespan=lifespan)

# --- Limitation de débit (anti-DoS / anti-brute-force) ---
# Clé = adresse IP du client. La limite par défaut s'applique à tous les
# endpoints décorés ; configurable via RATE_LIMIT (vide = désactivé).
_rate_limits = [settings.RATE_LIMIT] if settings.RATE_LIMIT else []
limiter = Limiter(key_func=get_remote_address, default_limits=_rate_limits)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=429,
        content={"detail": "Trop de requêtes. Réessayez plus tard."},
    )


class MetricsMiddleware(BaseHTTPMiddleware):
    """Compte les requêtes, les erreurs et la latence (observabilité, R13)."""

    async def dispatch(self, request: Request, call_next):
        metrics.inc("http_requests_total")
        with metrics.timer():
            response = await call_next(request)
        if response.status_code >= 500:
            metrics.inc("http_responses_5xx_total")
        elif response.status_code >= 400:
            metrics.inc("http_responses_4xx_total")
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Ajoute des en-têtes de sécurité à chaque réponse (API uniquement)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'")
        if settings.is_production():
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(MetricsMiddleware)

# Applique la limite par défaut à toutes les routes (si RATE_LIMIT défini).
if _rate_limits:
    app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["x-api-key", "content-type"],
)


# --- Auth simple par clé API (optionnelle) ---
async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Clé API invalide ou manquante.")


class ImagePayload(BaseModel):
    image: str


class RenamePayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ZonePayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    polygon: list[list[float]]
    enabled: bool = True


class AlertRulePayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    zone_id: int | None = None
    event_type: str = Field(default="face", min_length=1, max_length=40)
    notify: bool = True
    enabled: bool = True
    cooldown_seconds: float = Field(default=60, ge=0, le=86400)


def _maybe_auto_enroll(
    image_bytes: bytes, rect: dict, pitch: float, yaw: float, roll: float, frame_width: int
) -> dict | None:
    """Enrôle automatiquement un visage net non reconnu comme nouvelle référence.

    Retourne la référence créée, ou None si les conditions ne sont pas réunies.
    """
    if not settings.AUTO_ENROLL:
        return None
    # On désactive la vérification d'angle pour l'enrôlement (demande utilisateur)
    # Ignore les visages trop petits (qualité de référence insuffisante).
    face_w = rect.get("width", 0)
    if frame_width > 0 and face_w / frame_width < settings.AUTO_ENROLL_MIN_WIDTH_RATIO:
        return None

    image_path = recognition.save_reference_image(image_bytes, rect)
    label = database.next_auto_label()
    ref = database.add_reference(label, image_path, auto=True)
    metrics.inc("auto_enrollments_total")
    logger.info("Auto-enrôlement : nouvelle référence « %s » (id=%s).", mask_name(label), ref["id"])
    return ref


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "azure_configured": settings.azure_configured(),
        "deepface_available": recognition.DeepFace is not None,
        "references": len(database.list_references()),
        "recognition_action": settings.RECOGNITION_ACTION,
    }


@app.get("/metrics")
async def get_metrics() -> PlainTextResponse:
    """Métriques d'exploitation au format Prometheus (text/plain)."""
    return PlainTextResponse(metrics.render_prometheus())


@app.get("/settings")
async def get_settings() -> dict:
    return {
        "headpose_pitch_max": settings.HEADPOSE_PITCH_MAX,
        "headpose_yaw_max": settings.HEADPOSE_YAW_MAX,
        "recognition_action": settings.RECOGNITION_ACTION,
        "deepface_model": settings.DEEPFACE_MODEL,
    }


@app.get("/zones", dependencies=[Depends(require_api_key)])
async def get_zones() -> dict:
    return {"zones": database.list_zones()}


@app.post("/zones", dependencies=[Depends(require_api_key)])
async def add_zone(payload: ZonePayload) -> dict:
    try:
        polygon = zone_logic.validate_polygon(payload.polygon)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return database.create_zone(payload.name.strip(), polygon, payload.enabled)


@app.delete("/zones/{zone_id}", dependencies=[Depends(require_api_key)])
async def remove_zone(zone_id: int) -> dict:
    if not database.delete_zone(zone_id):
        raise HTTPException(status_code=404, detail="Zone introuvable.")
    return {"deleted": zone_id}


@app.get("/alerts", dependencies=[Depends(require_api_key)])
async def get_alert_rules() -> dict:
    return {"rules": database.list_alert_rules()}


@app.post("/alerts", dependencies=[Depends(require_api_key)])
async def add_alert_rule(payload: AlertRulePayload) -> dict:
    if payload.zone_id is not None and payload.zone_id not in {zone["id"] for zone in database.list_zones()}:
        raise HTTPException(status_code=404, detail="Zone introuvable.")
    return database.create_alert_rule(
        payload.name.strip(), payload.zone_id, payload.event_type,
        payload.notify, payload.enabled, payload.cooldown_seconds,
    )


@app.delete("/alerts/{rule_id}", dependencies=[Depends(require_api_key)])
async def remove_alert_rule(rule_id: int) -> dict:
    if not database.delete_alert_rule(rule_id):
        raise HTTPException(status_code=404, detail="Règle introuvable.")
    return {"deleted": rule_id}


def _decode_image(data_url: str) -> bytes:
    raw = data_url.split(",", 1)[1] if "," in data_url else data_url
    # Rejette tôt les payloads démesurés (4 caractères base64 ≈ 3 octets) afin
    # d'éviter d'allouer la mémoire du décodage pour un flux abusif.
    if len(raw) > (settings.MAX_IMAGE_BYTES // 3 + 1) * 4:
        raise HTTPException(status_code=413, detail="Image trop volumineuse.")
    try:
        data = base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Erreur de décodage base64 : {exc}")
    if len(data) > settings.MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image trop volumineuse.")
    if not _looks_like_image(data):
        raise HTTPException(status_code=400, detail="Format d'image non reconnu.")
    return data


@app.post("/analyze-face", dependencies=[Depends(require_api_key)])
async def analyze_face(payload: ImagePayload) -> dict:
    if not _analysis_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Une analyse est déjà en cours. Réessayez après sa fin.",
            headers={"Retry-After": "1"},
        )

    return await asyncio.to_thread(_analyze_face_locked, payload)


def _analyze_face_locked(payload: ImagePayload) -> dict:
    try:
        return _analyze_face(payload)
    finally:
        _analysis_lock.release()


def _analyze_face(payload: ImagePayload) -> dict:
    image_bytes = _decode_image(payload.image)

    # Les compteurs Azure (tentatives + échec final) sont gérés dans
    # recognition.detect_faces pour refléter chaque tentative de retry.
    try:
        detected = recognition.detect_faces(image_bytes)
    except recognition.EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except recognition.DetectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except requests_exc() as exc:
        raise HTTPException(status_code=502, detail="Erreur réseau vers Azure.") from exc

    if not detected:
        return {"faces": []}

    # Largeur de l'image (pour le seuil de taille minimale à l'auto-enrôlement).
    frame_width = 0
    if recognition.Image is not None:
        try:
            with recognition.Image.open(io.BytesIO(image_bytes)) as _img:
                frame_width = _img.size[0]
        except Exception:  # noqa: BLE001
            frame_width = 0

    frame_height = 0
    if recognition.Image is not None:
        try:
            with recognition.Image.open(io.BytesIO(image_bytes)) as _img:
                frame_height = _img.size[1]
        except Exception:  # noqa: BLE001
            frame_height = 0
    configured_zones = database.list_zones()
    faces_out = []
    track_ids = _face_tracker.update([face.get("faceRectangle", {}) for face in detected])
    for face, track_id in zip(detected, track_ids):
            rect = face.get("faceRectangle", {})
            zone_rect = {**rect, "frame_width": frame_width, "frame_height": frame_height}
            zone_ids = zone_logic.rectangle_zones(zone_rect, configured_zones)
            attrs = face.get("faceAttributes", {})
            pose = attrs.get("headPose", {})
            pitch = pose.get("pitch", 0.0)
            yaw = pose.get("yaw", 0.0)
            roll = pose.get("roll", 0.0)
            
            glasses = attrs.get("glasses", "NoGlasses")
            mask = attrs.get("mask")
            quality = attrs.get("quality")

            recognized = False
            confidence = 0.0
            name = None
            ref_id = None
            system_action = "None"

            # On ignore l'angle de tête pour la reconnaissance
            # Recadrage par visage : la vérification (et l'enrôlement) porte
            # sur ce visage précis, pas sur l'ensemble de la frame.
            face_path = recognition.save_temp_capture(image_bytes, rect)
            demographics = None
            try:
                recognized, confidence, matched = recognition.verify_against_references(face_path)
                demographics = recognition.analyze_demographics(face_path)
            except recognition.EngineUnavailableError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            finally:
                try:
                    if os.path.exists(face_path):
                        os.remove(face_path)
                except OSError as exc:
                    logger.warning("Nettoyage capture temporaire impossible : %s", exc)

            if recognized and matched:
                name = matched["name"]
                ref_id = matched["id"]
                system_action = trigger_action(name, confidence)
            elif recognition.DeepFace is None:
                system_action = "No reference / DeepFace missing"
            else:
                enrolled = _maybe_auto_enroll(image_bytes, rect, pitch, yaw, roll, frame_width)
                if enrolled:
                    recognized = False
                    confidence = 0.0
                    name = enrolled["name"]
                    ref_id = enrolled["id"]
                    system_action = "Auto-enrôlé"
                elif not database.list_references():
                    system_action = "No reference / DeepFace missing"
                else:
                    system_action = "Not recognized"

            alert_actions = []
            for rule in database.list_alert_rules():
                event_kind = "recognized" if recognized else "unknown"
                matches_type = rule["event_type"] in {"face", event_kind}
                matches_zone = rule["zone_id"] is None or rule["zone_id"] in zone_ids
                if matches_type and matches_zone:
                    alert_actions.append(trigger_alert(rule, {
                        "event_type": "face",
                        "track_id": track_id,
                        "name": name,
                        "recognized": recognized,
                        "zone_ids": zone_ids,
                    }))
            if alert_actions:
                system_action = f"{system_action}; {'; '.join(alert_actions)}"

            database.log_event(
                recognized=recognized,
                confidence=confidence,
                pitch=pitch,
                yaw=yaw,
                roll=roll,
                name=name,
                reference_id=ref_id,
                system_action=system_action,
                track_id=track_id,
                objects=[{"type": "face", "track_id": track_id, "zone_ids": zone_ids}],
            )

            faces_out.append(
                {
                    "faceRectangle": {
                        "top": rect.get("top", 0),
                        "left": rect.get("left", 0),
                        "width": rect.get("width", 0),
                        "height": rect.get("height", 0),
                    },
                    "recognized": recognized,
                    "confidence": round(confidence, 2),
                    "name": name,
                    "pitch": pitch,
                    "yaw": yaw,
                    "roll": roll,
                    "system_action": system_action,
                    "track_id": track_id,
                    "zone_ids": zone_ids,
                    "glasses": glasses,
                    "mask": mask,
                    "quality": quality,
                    "demographics": demographics
                }
            )

    return {"faces": faces_out}


@app.post("/references", dependencies=[Depends(require_api_key)])
async def enroll_reference(name: str = Form(...), file: UploadFile = File(...)) -> dict:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide.")
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux.")
    if not _looks_like_image(content):
        raise HTTPException(status_code=400, detail="Le fichier n'est pas une image valide.")
    database.init_db()  # garantit que REFERENCES_DIR existe
    # N'autorise qu'une extension connue, dérivée du contenu réel (pas du nom client).
    ext = ".jpg"
    if content.startswith(b"\x89PNG"):
        ext = ".png"
    elif content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        ext = ".webp"
    elif content.startswith(b"BM"):
        ext = ".bmp"
    safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_")) or "ref"
    dest = os.path.join(settings.REFERENCES_DIR, f"{safe_name}_{os.urandom(4).hex()}{ext}")
    with open(dest, "wb") as f:
        f.write(content)
    return database.add_reference(name, dest)


@app.get("/references", dependencies=[Depends(require_api_key)])
async def get_references() -> dict:
    return {"references": database.list_references()}


@app.patch("/references/{ref_id}", dependencies=[Depends(require_api_key)])
async def rename_reference(ref_id: int, payload: RenamePayload) -> dict:
    # `min_length` Pydantic s'applique avant strip : rejette un nom uniquement
    # composé d'espaces (ex. "   ") qui deviendrait vide après nettoyage.
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Le nom ne peut pas être vide.")
    updated = database.update_reference_name(ref_id, name)
    if updated is None:
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return updated


@app.get("/references/{ref_id}/image", dependencies=[Depends(require_api_key)])
async def get_reference_image(ref_id: int) -> FileResponse:
    ref = database.get_reference(ref_id)
    if not ref or not ref.get("image_path") or not os.path.exists(ref["image_path"]):
        raise HTTPException(status_code=404, detail="Image introuvable.")
    # Laisse Starlette déduire le type via l'extension (jpg/png/webp/bmp) plutôt
    # que forcer image/jpeg : les références manuelles ne sont pas toutes en JPEG.
    return FileResponse(ref["image_path"])


@app.delete("/references/{ref_id}", dependencies=[Depends(require_api_key)])
async def remove_reference(ref_id: int) -> dict:
    if not database.delete_reference(ref_id):
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return {"deleted": ref_id}


@app.get("/history", dependencies=[Depends(require_api_key)])
async def get_history(limit: int = 50) -> dict:
    # Borne le paramètre pour éviter les extractions massives.
    limit = max(1, min(limit, settings.HISTORY_LIMIT_MAX))
    return {"events": database.list_events(limit=limit)}


@app.get("/history/search", dependencies=[Depends(require_api_key)])
async def search_history(
    q: str | None = None,
    event_type: str | None = None,
    recognized: bool | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
) -> dict:
    return {
        "events": database.search_events(
            query=q,
            event_type=event_type,
            recognized=recognized,
            start=start,
            end=end,
            limit=limit,
        )
    }


def requests_exc():
    """Tuple d'exceptions réseau requests (import paresseux pour faciliter les tests)."""
    import requests

    return requests.exceptions.RequestException


class CameraProxyPayload(BaseModel):
    url: str
    username: str = ""
    password: str = ""


@app.post("/proxy-camera", dependencies=[Depends(require_api_key)])
async def proxy_camera(payload: CameraProxyPayload) -> dict:
    """Proxy pour récupérer une image depuis une caméra IP en contournant les erreurs CORS."""
    import requests
    from requests.auth import HTTPBasicAuth
    
    try:
        auth = HTTPBasicAuth(payload.username, payload.password) if payload.username else None
        # Timeout court (5s) pour ne pas bloquer l'API si la caméra est éteinte, on utilise stream=True pour gérer les flux vidéo
        resp = requests.get(payload.url, auth=auth, timeout=5, stream=True)
        resp.raise_for_status()
        
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" in content_type.lower():
            raise HTTPException(
                status_code=400, 
                detail="L'URL fournie renvoie une page web (interface de la caméra) au lieu d'une image. Pour les caméras D-Link, assurez-vous de pointer vers le flux image, par exemple : http://votre-ip/image/jpeg.cgi"
            )
            
        # Si c'est un flux vidéo MJPEG (ex: video.cgi), on extrait juste la première image (JPEG)
        if "multipart" in content_type.lower() or "mixed-replace" in content_type.lower():
            image_bytes = b""
            for chunk in resp.iter_content(chunk_size=4096):
                if chunk:
                    image_bytes += chunk
                    start = image_bytes.find(b"\xff\xd8")
                    end = image_bytes.find(b"\xff\xd9")
                    if start != -1 and end != -1 and end > start:
                        image_bytes = image_bytes[start:end+2]
                        break
                    # Sécurité pour ne pas télécharger indéfiniment si pas de JPEG trouvé
                    if len(image_bytes) > 2 * 1024 * 1024:
                        raise HTTPException(status_code=502, detail="Impossible d'extraire une image du flux vidéo.")
            mime_type = "image/jpeg"
        else:
            # C'est une simple image (ex: image/jpeg.cgi)
            image_bytes = resp.content
            mime_type = content_type if "image/" in content_type.lower() else "image/jpeg"

        if not image_bytes:
            raise HTTPException(status_code=502, detail="Flux vidéo ou image vide.")

        b64 = base64.b64encode(image_bytes).decode("utf-8")
        return {"image": f"data:{mime_type};base64,{b64}"}
        
    except requests.exceptions.RequestException as e:
        logger.error("Erreur proxy caméra IP: %s", e)
        raise HTTPException(status_code=502, detail=f"Impossible de joindre la caméra: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Erreur inattendue proxy caméra: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
