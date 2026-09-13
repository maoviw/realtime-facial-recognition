"""Configuration centralisée et validée pour l'API de reconnaissance faciale."""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings:
    """Paramètres de l'application, chargés depuis l'environnement."""

    # --- Azure Face API ---
    AZURE_FACE_ENDPOINT: str = os.getenv("AZURE_FACE_ENDPOINT", "")
    AZURE_FACE_KEY: str = os.getenv("AZURE_FACE_KEY", "")
    AZURE_TIMEOUT: float = _get_float("AZURE_TIMEOUT", 10.0)
    # Résilience Azure : retries avec backoff exponentiel sur erreurs transitoires.
    AZURE_MAX_RETRIES: int = _get_int("AZURE_MAX_RETRIES", 2)
    AZURE_BACKOFF_BASE: float = _get_float("AZURE_BACKOFF_BASE", 0.5)

    # --- Stockage ---
    DATA_DIR: str = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
    DB_PATH: str = os.getenv("DB_PATH", os.path.join(DATA_DIR, "app.db"))
    REFERENCES_DIR: str = os.getenv("REFERENCES_DIR", os.path.join(DATA_DIR, "references"))

    # --- Reconnaissance ---
    # Seuils head pose (regard direct)
    HEADPOSE_PITCH_MAX: float = _get_float("HEADPOSE_PITCH_MAX", 15.0)
    HEADPOSE_YAW_MAX: float = _get_float("HEADPOSE_YAW_MAX", 15.0)
    # Modèle DeepFace
    DEEPFACE_MODEL: str = os.getenv("DEEPFACE_MODEL", "VGG-Face")
    # Garde-fou anti-blocage : durée max d'une vérification DeepFace (secondes).
    DEEPFACE_TIMEOUT: float = _get_float("DEEPFACE_TIMEOUT", 20.0)

    # --- Auto-enrôlement ---
    # Quand un visage net (angle franc) n'est pas reconnu, il est automatiquement
    # enregistré comme nouvelle référence « Visage N ».
    AUTO_ENROLL: bool = _get_bool("AUTO_ENROLL", True)
    # Seuils head pose stricts pour ne capturer qu'un visage de face bien net.
    AUTO_ENROLL_PITCH_MAX: float = _get_float("AUTO_ENROLL_PITCH_MAX", 8.0)
    AUTO_ENROLL_YAW_MAX: float = _get_float("AUTO_ENROLL_YAW_MAX", 8.0)
    AUTO_ENROLL_ROLL_MAX: float = _get_float("AUTO_ENROLL_ROLL_MAX", 12.0)
    # Ne pas auto-enrôler un visage trop petit (qualité insuffisante). Abaissé à 0.02 pour capturer de plus loin.
    AUTO_ENROLL_MIN_WIDTH_RATIO: float = _get_float("AUTO_ENROLL_MIN_WIDTH_RATIO", 0.02)

    # --- Action déclenchée à la reconnaissance ---
    # Valeurs: none | log | command | webhook
    RECOGNITION_ACTION: str = os.getenv("RECOGNITION_ACTION", "log").lower()
    RECOGNITION_COMMAND: str = os.getenv("RECOGNITION_COMMAND", "")
    RECOGNITION_WEBHOOK_URL: str = os.getenv("RECOGNITION_WEBHOOK_URL", "")
    RECOGNITION_WEBHOOK_COOLDOWN: float = _get_float("RECOGNITION_WEBHOOK_COOLDOWN", 60.0)
    # L'exécution de commandes arbitraires (RECOGNITION_ACTION=command) est une
    # surface d'abus : elle doit être explicitement activée par l'opérateur.
    ALLOW_COMMAND_ACTION: bool = _get_bool("ALLOW_COMMAND_ACTION", False)

    # --- Sécurité / API ---
    API_KEY: str = os.getenv("API_KEY", "")  # vide = pas d'auth
    # En production, refuser le démarrage si aucune clé API n'est définie.
    ENV: str = os.getenv("ENV", "development").lower()
    CORS_ORIGINS: list[str] = _get_list("CORS_ORIGINS", "http://localhost:3000")

    # --- Limites d'entrée (anti-DoS) ---
    MAX_IMAGE_BYTES: int = _get_int("MAX_IMAGE_BYTES", 8 * 1024 * 1024)  # 8 Mio
    MAX_UPLOAD_BYTES: int = _get_int("MAX_UPLOAD_BYTES", 8 * 1024 * 1024)  # 8 Mio
    HISTORY_LIMIT_MAX: int = _get_int("HISTORY_LIMIT_MAX", 200)

    # --- Rétention de l'historique (anti-croissance illimitée) ---
    # Purge des événements plus vieux que N jours (0 = pas de purge par âge).
    HISTORY_TTL_DAYS: int = _get_int("HISTORY_TTL_DAYS", 30)
    # Plafond dur du nombre de lignes conservées (0 = illimité).
    HISTORY_MAX_ROWS: int = _get_int("HISTORY_MAX_ROWS", 10000)

    # --- Limitation de débit (anti-DoS / anti-brute-force) ---
    # Format slowapi : "<nombre>/<période>" (ex. "60/minute", "5/second").
    # Vide pour désactiver.
    RATE_LIMIT: str = os.getenv("RATE_LIMIT", "60/minute")

    # --- Journalisation / conformité ---
    # Masque les noms (PII) dans les logs ; désactiver uniquement en dev.
    LOG_MASK_PII: bool = _get_bool("LOG_MASK_PII", True)
    # Format des logs : "text" (lisible) ou "json" (structuré, pour ingestion).
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "text").lower()
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    @classmethod
    def azure_configured(cls) -> bool:
        return bool(cls.AZURE_FACE_ENDPOINT and cls.AZURE_FACE_KEY)

    @classmethod
    def is_production(cls) -> bool:
        return cls.ENV in ("production", "prod")


def mask_name(name: str | None) -> str:
    """Masque un nom pour la journalisation (conformité PII).

    Conserve la première lettre et la longueur (ex. « Alice » -> « A**** »),
    sauf si LOG_MASK_PII est désactivé (renvoie alors le nom en clair).
    """
    label = name or "Inconnu"
    if not settings.LOG_MASK_PII:
        return label
    if len(label) <= 1:
        return "*"
    return label[0] + "*" * (len(label) - 1)


settings = Settings()
