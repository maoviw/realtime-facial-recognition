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
import base64
import io
import logging
import os
from contextlib import asynccontextmanager
from threading import Lock

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import database
import recognition
from actions import trigger_action
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("recognition.api")
_analysis_lock = Lock()


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
    logger.info("Démarrage OK — %d référence(s) enrôlée(s).", len(database.list_references()))
    yield


app = FastAPI(title="Real-time Facial Recognition API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Auth simple par clé API (optionnelle) ---
async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Clé API invalide ou manquante.")


class ImagePayload(BaseModel):
    image: str


class RenamePayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)


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
    logger.info("Auto-enrôlement : nouvelle référence « %s » (id=%s).", label, ref["id"])
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


@app.get("/settings")
async def get_settings() -> dict:
    return {
        "headpose_pitch_max": settings.HEADPOSE_PITCH_MAX,
        "headpose_yaw_max": settings.HEADPOSE_YAW_MAX,
        "recognition_action": settings.RECOGNITION_ACTION,
        "deepface_model": settings.DEEPFACE_MODEL,
    }


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
def analyze_face(payload: ImagePayload) -> dict:
    if not _analysis_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Une analyse est déjà en cours. Réessayez après sa fin.",
            headers={"Retry-After": "1"},
        )
    try:
        return _analyze_face(payload)
    finally:
        _analysis_lock.release()


def _analyze_face(payload: ImagePayload) -> dict:
    image_bytes = _decode_image(payload.image)

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

    faces_out = []
    for face in detected:
            rect = face.get("faceRectangle", {})
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
                if os.path.exists(face_path):
                    os.remove(face_path)

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

            database.log_event(
                recognized=recognized,
                confidence=confidence,
                pitch=pitch,
                yaw=yaw,
                roll=roll,
                name=name,
                reference_id=ref_id,
                system_action=system_action,
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
    updated = database.update_reference_name(ref_id, payload.name.strip())
    if updated is None:
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return updated


@app.get("/references/{ref_id}/image", dependencies=[Depends(require_api_key)])
async def get_reference_image(ref_id: int) -> FileResponse:
    ref = database.get_reference(ref_id)
    if not ref or not ref.get("image_path") or not os.path.exists(ref["image_path"]):
        raise HTTPException(status_code=404, detail="Image introuvable.")
    return FileResponse(ref["image_path"], media_type="image/jpeg")


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
