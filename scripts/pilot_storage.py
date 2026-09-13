import os
from pathlib import Path
import shutil
import stat


MIB = 1024 * 1024


class StorageBudget:
    def __init__(self, root, quota_bytes=512 * MIB, reserve_bytes=2048 * MIB, headroom_bytes=32 * MIB):
        if quota_bytes <= headroom_bytes or reserve_bytes < 0 or headroom_bytes <= 0:
            raise ValueError("Invalid storage budget.")
        self.root = Path(root)
        self.quota_bytes = quota_bytes
        self.reserve_bytes = reserve_bytes
        self.headroom_bytes = headroom_bytes
        self.observed = {"checks": 0, "peak_used_bytes": 0, "min_free_bytes": None}

    def check(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if self._is_link(self.root):
            raise RuntimeError("Storage root must not be a link.")
        used_bytes = 0
        for directory, directories, files in os.walk(self.root, followlinks=False, onerror=self._scan_error):
            for name in directories + files:
                path = Path(directory) / name
                try:
                    if self._is_link(path):
                        raise RuntimeError("Storage must not contain links.")
                    if name in files:
                        used_bytes += path.stat().st_size
                except FileNotFoundError:
                    pass
        free_bytes = shutil.disk_usage(self.root).free
        self.observed["checks"] += 1
        self.observed["peak_used_bytes"] = max(self.observed["peak_used_bytes"], used_bytes)
        previous_free = self.observed["min_free_bytes"]
        self.observed["min_free_bytes"] = free_bytes if previous_free is None else min(previous_free, free_bytes)
        if used_bytes + self.headroom_bytes > self.quota_bytes:
            raise RuntimeError("Pilot storage quota reached; existing captures were preserved.")
        if free_bytes < self.reserve_bytes + self.headroom_bytes:
            raise RuntimeError("Pilot free-space reserve reached; existing captures were preserved.")
        return {"used_bytes": used_bytes, "free_bytes": free_bytes}

    @staticmethod
    def _is_link(path):
        return path.is_symlink() or bool(
            getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )

    def __enter__(self):
        self.check()
        self.lock = self.root / ".pilot-writer.lock"
        try:
            descriptor = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RuntimeError("Another pilot owns the storage lock; no capture started.") from None
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(str(os.getpid()))
        except BaseException:
            self.lock.unlink()
            raise
        return self

    def __exit__(self, *args):
        self.lock.unlink()

    @staticmethod
    def _scan_error(error):
        raise error