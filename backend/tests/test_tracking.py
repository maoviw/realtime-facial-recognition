from tracking import FaceTracker


def test_face_tracker_keeps_id_for_overlapping_rectangle():
    tracker = FaceTracker(ttl_seconds=10, minimum_iou=0.2)
    first = tracker.update([{"left": 10, "top": 10, "width": 40, "height": 40}], now=0)
    second = tracker.update([{"left": 12, "top": 11, "width": 40, "height": 40}], now=1)
    assert first == ["face-1"]
    assert second == first


def test_face_tracker_expires_old_id():
    tracker = FaceTracker(ttl_seconds=2)
    assert tracker.update([{"left": 0, "top": 0, "width": 20, "height": 20}], now=0) == ["face-1"]
    assert tracker.update([{"left": 0, "top": 0, "width": 20, "height": 20}], now=3) == ["face-2"]
