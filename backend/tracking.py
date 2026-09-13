"""Suivi léger de visages entre captures successives.

Le tracker est volontairement local et borné : il ne prétend pas remplacer un
modèle multi-objet, mais conserve un identifiant stable pour les événements
facials rapprochés sans ajouter de dépendance GPU.
"""
from dataclasses import dataclass
import time


@dataclass
class _Track:
    track_id: str
    rect: dict
    updated_at: float


def _iou(first: dict, second: dict) -> float:
    left = max(first.get("left", 0), second.get("left", 0))
    top = max(first.get("top", 0), second.get("top", 0))
    right = min(first.get("left", 0) + first.get("width", 0), second.get("left", 0) + second.get("width", 0))
    bottom = min(first.get("top", 0) + first.get("height", 0), second.get("top", 0) + second.get("height", 0))
    intersection = max(0, right - left) * max(0, bottom - top)
    first_area = max(0, first.get("width", 0)) * max(0, first.get("height", 0))
    second_area = max(0, second.get("width", 0)) * max(0, second.get("height", 0))
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


class FaceTracker:
    def __init__(self, ttl_seconds: float = 12.0, minimum_iou: float = 0.2) -> None:
        self.ttl_seconds = ttl_seconds
        self.minimum_iou = minimum_iou
        self._tracks: list[_Track] = []
        self._next_id = 1

    def update(self, rectangles: list[dict], now: float | None = None) -> list[str]:
        timestamp = time.monotonic() if now is None else now
        self._tracks = [track for track in self._tracks if timestamp - track.updated_at <= self.ttl_seconds]
        assigned: set[str] = set()
        result: list[str] = []
        for rect in rectangles:
            candidates = [
                track for track in self._tracks
                if track.track_id not in assigned and _iou(track.rect, rect) >= self.minimum_iou
            ]
            track = max(candidates, key=lambda item: _iou(item.rect, rect), default=None)
            if track is None:
                track = _Track(f"face-{self._next_id}", rect, timestamp)
                self._next_id += 1
                self._tracks.append(track)
            else:
                track.rect = rect
                track.updated_at = timestamp
            assigned.add(track.track_id)
            result.append(track.track_id)
        return result
