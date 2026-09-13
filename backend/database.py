"""Persistance SQLite : visages de référence enrôlés et historique des reconnaissances."""
import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from config import settings


def _ensure_dirs() -> None:
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    os.makedirs(settings.REFERENCES_DIR, exist_ok=True)


@contextmanager
def get_connection():
    _ensure_dirs()
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Crée les tables si elles n'existent pas, puis applique les migrations."""
    _ensure_dirs()
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS references_face (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                image_path TEXT NOT NULL,
                auto INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recognition_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reference_id INTEGER,
                name TEXT,
                recognized INTEGER NOT NULL,
                confidence REAL NOT NULL,
                pitch REAL,
                yaw REAL,
                roll REAL,
                system_action TEXT,
                event_type TEXT NOT NULL DEFAULT 'face',
                track_id TEXT,
                objects_json TEXT,
                summary TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                polygon_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                zone_id INTEGER,
                event_type TEXT NOT NULL DEFAULT 'face',
                notify INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                cooldown_seconds REAL NOT NULL DEFAULT 60,
                created_at TEXT NOT NULL
            );
            """
        )
        # Migration : ajoute la colonne `auto` aux bases antérieures.
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(references_face)")}
        if "auto" not in cols:
            conn.execute("ALTER TABLE references_face ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")

        event_cols = {row["name"] for row in conn.execute("PRAGMA table_info(recognition_events)")}
        migrations = {
            "event_type": "ALTER TABLE recognition_events ADD COLUMN event_type TEXT NOT NULL DEFAULT 'face'",
            "track_id": "ALTER TABLE recognition_events ADD COLUMN track_id TEXT",
            "objects_json": "ALTER TABLE recognition_events ADD COLUMN objects_json TEXT",
            "summary": "ALTER TABLE recognition_events ADD COLUMN summary TEXT",
        }
        for column, statement in migrations.items():
            if column not in event_cols:
                conn.execute(statement)

        # Index sur les colonnes fréquemment filtrées/triées (R11).
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_events_created_at
                ON recognition_events (created_at);
            CREATE INDEX IF NOT EXISTS idx_events_reference_id
                ON recognition_events (reference_id);
            CREATE INDEX IF NOT EXISTS idx_events_type_created_at
                ON recognition_events (event_type, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_track_id
                ON recognition_events (track_id);
            """
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_zone(name: str, polygon: list[list[float]], enabled: bool = True) -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO zones (name, polygon_json, enabled, created_at) VALUES (?, ?, ?, ?)",
            (name, json.dumps(polygon), int(enabled), _now()),
        )
    return {"id": cur.lastrowid, "name": name, "polygon": polygon, "enabled": enabled}


def list_zones() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM zones ORDER BY created_at DESC, id DESC").fetchall()
    zones = []
    for row in rows:
        zone = dict(row)
        zone.pop("polygon_json", None)
        zone["polygon"] = json.loads(row["polygon_json"])
        zone["enabled"] = bool(row["enabled"])
        zones.append(zone)
    return zones


def delete_zone(zone_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM zones WHERE id = ?", (zone_id,))
        conn.execute("UPDATE alert_rules SET zone_id = NULL WHERE zone_id = ?", (zone_id,))
    return bool(cur.rowcount)


def create_alert_rule(
    name: str, zone_id: int | None, event_type: str, notify: bool, enabled: bool, cooldown_seconds: float
) -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO alert_rules
               (name, zone_id, event_type, notify, enabled, cooldown_seconds, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, zone_id, event_type, int(notify), int(enabled), cooldown_seconds, _now()),
        )
    return {
        "id": cur.lastrowid, "name": name, "zone_id": zone_id, "event_type": event_type,
        "notify": notify, "enabled": enabled, "cooldown_seconds": cooldown_seconds,
    }


def list_alert_rules() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM alert_rules ORDER BY created_at DESC, id DESC").fetchall()
    return [
        {**dict(row), "notify": bool(row["notify"]), "enabled": bool(row["enabled"])}
        for row in rows
    ]


def delete_alert_rule(rule_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
    return bool(cur.rowcount)


# --- Compteur persistant (libellés auto « Visage N ») ---

def next_auto_label() -> str:
    """Retourne le prochain libellé automatique « Visage N » (compteur monotone)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'auto_face_seq'"
        ).fetchone()
        current = int(row["value"]) if row else 0
        nxt = current + 1
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('auto_face_seq', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(nxt),),
        )
    return f"Visage {nxt}"


# --- Références ---

def add_reference(name: str, image_path: str, auto: bool = False) -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO references_face (name, image_path, auto, created_at) VALUES (?, ?, ?, ?)",
            (name, image_path, 1 if auto else 0, _now()),
        )
        ref_id = cur.lastrowid
    # N'expose pas image_path (chemin filesystem interne) dans les réponses API.
    return {"id": ref_id, "name": name, "auto": auto}


def update_reference_name(ref_id: int, name: str) -> dict | None:
    """Renomme une référence. La marque comme non-automatique (nommée manuellement)."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE references_face SET name = ?, auto = 0 WHERE id = ?",
            (name, ref_id),
        )
        if cur.rowcount == 0:
            return None
    return {"id": ref_id, "name": name, "auto": False}


def list_references() -> list[dict]:
    """Liste publique des références — sans le chemin filesystem interne."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, auto, created_at FROM references_face ORDER BY created_at DESC"
        ).fetchall()
    refs = []
    for row in rows:
        ref = dict(row)
        ref["auto"] = bool(ref["auto"])
        refs.append(ref)
    return refs


def list_references_internal() -> list[dict]:
    """Usage interne : inclut image_path (jamais exposé via l'API)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, image_path, auto, created_at FROM references_face ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def get_reference(ref_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, image_path, created_at FROM references_face WHERE id = ?",
            (ref_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_reference(ref_id: int) -> bool:
    ref = get_reference(ref_id)
    if not ref:
        return False
    with get_connection() as conn:
        conn.execute("DELETE FROM references_face WHERE id = ?", (ref_id,))
        # Évite des événements pointant vers une référence supprimée (intégrité, R11).
        conn.execute(
            "UPDATE recognition_events SET reference_id = NULL WHERE reference_id = ?",
            (ref_id,),
        )
    # Suppression du fichier image associé
    try:
        if ref["image_path"] and os.path.exists(ref["image_path"]):
            os.remove(ref["image_path"])
    except OSError:
        pass
    return True


# --- Historique ---

def log_event(
    recognized: bool,
    confidence: float,
    pitch: float | None = None,
    yaw: float | None = None,
    roll: float | None = None,
    name: str | None = None,
    reference_id: int | None = None,
    system_action: str | None = None,
    event_type: str = "face",
    track_id: str | None = None,
    objects: list[dict] | None = None,
    summary: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO recognition_events
               (reference_id, name, recognized, confidence, pitch, yaw, roll, system_action,
                event_type, track_id, objects_json, summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                reference_id,
                name,
                1 if recognized else 0,
                confidence,
                pitch,
                yaw,
                roll,
                system_action,
                event_type,
                track_id,
                json.dumps(objects, ensure_ascii=False) if objects is not None else None,
                summary,
                _now(),
            ),
        )


def list_events(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM recognition_events ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    events = []
    for row in rows:
        event = dict(row)
        event["recognized"] = bool(event["recognized"])
        event["objects"] = json.loads(event.pop("objects_json") or "[]")
        events.append(event)
    return events


def search_events(
    *,
    query: str | None = None,
    event_type: str | None = None,
    recognized: bool | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Recherche structurée dans l'historique, sans charger toute la base."""
    clauses: list[str] = []
    params: list[object] = []
    if query:
        clauses.append("(name LIKE ? OR system_action LIKE ? OR summary LIKE ? OR objects_json LIKE ?)")
        pattern = f"%{query}%"
        params.extend([pattern] * 4)
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if recognized is not None:
        clauses.append("recognized = ?")
        params.append(1 if recognized else 0)
    if start:
        clauses.append("created_at >= ?")
        params.append(start)
    if end:
        clauses.append("created_at <= ?")
        params.append(end)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM recognition_events {where} ORDER BY created_at DESC, id DESC LIMIT ?",
            (*params, max(1, min(limit, 500))),
        ).fetchall()
    events = []
    for row in rows:
        event = dict(row)
        event["recognized"] = bool(event["recognized"])
        event["objects"] = json.loads(event.pop("objects_json") or "[]")
        events.append(event)
    return events


def purge_old_events(ttl_days: int | None = None, max_rows: int | None = None) -> int:
    """Purge l'historique : supprime les événements trop anciens et/ou au-delà
    d'un plafond de lignes. Retourne le nombre de lignes supprimées.

    - `ttl_days` (défaut `HISTORY_TTL_DAYS`) : supprime les événements plus
      vieux que N jours. 0 désactive la purge par âge.
    - `max_rows` (défaut `HISTORY_MAX_ROWS`) : ne conserve que les N plus
      récents. 0 désactive le plafond.
    """
    ttl_days = settings.HISTORY_TTL_DAYS if ttl_days is None else ttl_days
    max_rows = settings.HISTORY_MAX_ROWS if max_rows is None else max_rows
    deleted = 0
    with get_connection() as conn:
        if ttl_days and ttl_days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
            cur = conn.execute(
                "DELETE FROM recognition_events WHERE created_at < ?", (cutoff,)
            )
            deleted += cur.rowcount or 0
        if max_rows and max_rows > 0:
            cur = conn.execute(
                """DELETE FROM recognition_events
                   WHERE id NOT IN (
                       SELECT id FROM recognition_events
                       ORDER BY created_at DESC LIMIT ?
                   )""",
                (max_rows,),
            )
            deleted += cur.rowcount or 0
    return deleted
