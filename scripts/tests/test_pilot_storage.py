from types import SimpleNamespace

import pytest

from scripts import pilot_storage


def test_budget_counts_nested_files_and_keeps_existing_captures(tmp_path, monkeypatch):
    capture = tmp_path / "capture"
    capture.mkdir()
    recording = capture / "segment.m4s"
    recording.write_bytes(b"data")
    monkeypatch.setattr(pilot_storage.shutil, "disk_usage", lambda root: SimpleNamespace(free=100))
    budget = pilot_storage.StorageBudget(tmp_path, quota_bytes=20, reserve_bytes=50, headroom_bytes=10)
    assert budget.check() == {"used_bytes": 4, "free_bytes": 100}
    recording.write_bytes(b"x" * 11)
    with pytest.raises(RuntimeError, match="quota"):
        budget.check()
    assert recording.read_bytes() == b"x" * 11
    assert budget.observed == {"checks": 2, "peak_used_bytes": 11, "min_free_bytes": 100}


def test_budget_rejects_low_free_space(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot_storage.shutil, "disk_usage", lambda root: SimpleNamespace(free=59))
    with pytest.raises(RuntimeError, match="reserve"):
        pilot_storage.StorageBudget(tmp_path, 100, 50, 10).check()


def test_budget_accepts_exact_boundary(tmp_path, monkeypatch):
    (tmp_path / "segment.tmp").write_bytes(b"x" * 10)
    monkeypatch.setattr(pilot_storage.shutil, "disk_usage", lambda root: SimpleNamespace(free=60))
    assert pilot_storage.StorageBudget(tmp_path, 20, 50, 10).check()["used_bytes"] == 10


def test_budget_rejects_links(tmp_path, monkeypatch):
    (tmp_path / "untrusted").mkdir()
    monkeypatch.setattr(pilot_storage.Path, "is_symlink", lambda path: path.name == "untrusted")
    with pytest.raises(RuntimeError, match="links"):
        pilot_storage.StorageBudget(tmp_path).check()


def test_budget_fails_closed_on_scan_error(tmp_path, monkeypatch):
    def inaccessible(root, followlinks, onerror):
        onerror(PermissionError("denied"))
        return []

    monkeypatch.setattr(pilot_storage.os, "walk", inaccessible)
    with pytest.raises(PermissionError):
        pilot_storage.StorageBudget(tmp_path).check()


def test_budget_exclusive_lock_released_after_error(tmp_path):
    budget = pilot_storage.StorageBudget(tmp_path, reserve_bytes=0)
    with pytest.raises(ValueError):
        with budget:
            with pytest.raises(RuntimeError, match="lock"):
                with pilot_storage.StorageBudget(tmp_path, reserve_bytes=0):
                    pytest.fail("Concurrent writer admitted")
            raise ValueError("interrupted")
    assert not (tmp_path / ".pilot-writer.lock").exists()


def test_existing_lock_is_never_removed(tmp_path):
    lock = tmp_path / ".pilot-writer.lock"
    lock.write_text("previous process")
    with pytest.raises(RuntimeError, match="lock"):
        with pilot_storage.StorageBudget(tmp_path, reserve_bytes=0):
            pytest.fail("Stale lock admitted")
    assert lock.read_text() == "previous process"


@pytest.mark.parametrize("limits", [(10, 0, 10), (20, -1, 10), (20, 0, 0)])
def test_budget_rejects_invalid_limits(tmp_path, limits):
    with pytest.raises(ValueError):
        pilot_storage.StorageBudget(tmp_path, *limits)