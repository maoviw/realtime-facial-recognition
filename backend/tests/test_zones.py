import pytest

from zones import contains_point, rectangle_zones, validate_polygon


def test_zone_validation_and_point_membership():
    polygon = validate_polygon([[0, 0], [1, 0], [1, 1], [0, 1]])
    assert contains_point((0.5, 0.5), polygon)
    assert not contains_point((1.2, 0.5), polygon)


def test_rectangle_zones_uses_face_center():
    zones = [{"id": 4, "enabled": True, "polygon": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]}]
    assert rectangle_zones(
        {"left": 10, "top": 10, "width": 20, "height": 20, "frame_width": 100, "frame_height": 100},
        zones,
    ) == [4]


def test_zone_validation_rejects_out_of_range_point():
    with pytest.raises(ValueError):
        validate_polygon([[0, 0], [1.2, 0], [1, 1]])
