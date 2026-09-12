import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

_BOOTSTRAP = tempfile.TemporaryDirectory(prefix="frt_test_")
_TEST_ENV = {
    "DATA_DIR": _BOOTSTRAP.name,
    "DB_PATH": os.path.join(_BOOTSTRAP.name, "test.db"),
    "REFERENCES_DIR": os.path.join(_BOOTSTRAP.name, "refs"),
    "AZURE_FACE_ENDPOINT": "https://example.test",
    "AZURE_FACE_KEY": "test-key",
    "RECOGNITION_ACTION": "log",
    "API_KEY": "",
    "ENV": "development",
}

from fastapi.testclient import TestClient  # noqa: E402

_ORIGINAL_DEEPFACE = sys.modules.get("deepface")
sys.modules["deepface"] = SimpleNamespace(DeepFace=None)

with (
    patch.dict(os.environ, _TEST_ENV, clear=True),
    patch("dotenv.load_dotenv", return_value=False),
):
    import database
    import main
    import recognition
    from config import settings


def pytest_unconfigure(config):
    if _ORIGINAL_DEEPFACE is None:
        sys.modules.pop("deepface", None)
    else:
        sys.modules["deepface"] = _ORIGINAL_DEEPFACE
    _BOOTSTRAP.cleanup()


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "REFERENCES_DIR", str(tmp_path / "refs"))
    monkeypatch.setattr(recognition, "DeepFace", object())
    monkeypatch.setattr(recognition, "detect_faces", Mock(side_effect=AssertionError("Mock Azure detection explicitly")))
    monkeypatch.setattr(recognition, "verify_against_references", Mock(return_value=(False, 0.0, None)))
    monkeypatch.setattr(recognition, "analyze_demographics", Mock(return_value=None))
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        Mock(side_effect=AssertionError("Network calls are disabled in API tests")),
    )


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client
