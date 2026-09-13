"""Validation et géométrie des zones normalisées par caméra."""


def validate_polygon(polygon: list[list[float]]) -> list[list[float]]:
    if len(polygon) < 3:
        raise ValueError("Une zone doit contenir au moins trois points.")
    normalized: list[list[float]] = []
    for point in polygon:
        if len(point) != 2 or not all(0 <= float(value) <= 1 for value in point):
            raise ValueError("Les points d'une zone doivent être normalisés entre 0 et 1.")
        normalized.append([float(point[0]), float(point[1])])
    return normalized


def contains_point(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    """Test point-polygone par ray casting."""
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = current
        x2, y2 = previous
        crosses = (y1 > y) != (y2 > y)
        if crosses and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
        previous = current
    return inside


def rectangle_zones(rectangle: dict, polygons: list[dict]) -> list[int]:
    center = (
        float(rectangle.get("left", 0)) + float(rectangle.get("width", 0)) / 2,
        float(rectangle.get("top", 0)) + float(rectangle.get("height", 0)) / 2,
    )
    frame_width = max(float(rectangle.get("frame_width", 1)), 1)
    frame_height = max(float(rectangle.get("frame_height", 1)), 1)
    point = (center[0] / frame_width, center[1] / frame_height)
    return [zone["id"] for zone in polygons if zone.get("enabled", True) and contains_point(point, zone["polygon"])]
