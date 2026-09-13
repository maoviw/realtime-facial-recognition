"""Métriques d'exploitation in-process (sans dépendance externe).

Expose un petit registre de compteurs/jauges et un rendu au format
d'exposition Prometheus (text/plain), consommé par l'endpoint `/metrics`.
Volontairement minimal : pas de labels à forte cardinalité.
"""
import threading
import time

_lock = threading.Lock()

# Compteurs cumulatifs (monotones).
_counters: dict[str, float] = {
    "http_requests_total": 0.0,
    "http_responses_5xx_total": 0.0,
    "http_responses_4xx_total": 0.0,
    "azure_requests_total": 0.0,
    "azure_failures_total": 0.0,
    "deepface_timeouts_total": 0.0,
    "auto_enrollments_total": 0.0,
    "request_latency_seconds_sum": 0.0,
    "request_latency_seconds_count": 0.0,
}

_HELP = {
    "http_requests_total": ("counter", "Nombre total de requêtes HTTP reçues."),
    "http_responses_5xx_total": ("counter", "Réponses HTTP 5xx."),
    "http_responses_4xx_total": ("counter", "Réponses HTTP 4xx."),
    "azure_requests_total": ("counter", "Appels Azure Face Detect tentés."),
    "azure_failures_total": ("counter", "Appels Azure ayant échoué après retries."),
    "deepface_timeouts_total": ("counter", "Vérifications DeepFace abandonnées sur timeout."),
    "auto_enrollments_total": ("counter", "Visages auto-enrôlés."),
    "request_latency_seconds_sum": ("counter", "Somme des latences de requête (s)."),
    "request_latency_seconds_count": ("counter", "Nombre de requêtes mesurées."),
}


def inc(name: str, value: float = 1.0) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0.0) + value


def observe_latency(seconds: float) -> None:
    with _lock:
        _counters["request_latency_seconds_sum"] += seconds
        _counters["request_latency_seconds_count"] += 1.0


def snapshot() -> dict[str, float]:
    with _lock:
        return dict(_counters)


def render_prometheus() -> str:
    """Rend les métriques au format d'exposition Prometheus (text/plain)."""
    data = snapshot()
    lines: list[str] = []
    for name, value in data.items():
        kind, help_text = _HELP.get(name, ("counter", name))
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


class _Timer:
    """Context manager mesurant la durée et l'incrémentant dans les métriques."""

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        observe_latency(time.perf_counter() - self._start)
        return False


def timer() -> _Timer:
    return _Timer()
