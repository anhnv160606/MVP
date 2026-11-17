import base64
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError
from storage3.exceptions import StorageApiError

from api.app import create_app
from config.settings import Settings
from database.readiness import check_backend
from memory.project import ProjectMemory


@pytest.mark.parametrize(
    "key,expected",
    [
        ("sb_publishable_example", False),
        ("sb_secret_example", True),
        ("not-a-server-key", False),
        ("a.invalid.c", False),
    ],
)
def test_server_key_detection(key, expected):
    settings = Settings(
        _env_file=None, supabase_url="https://example.supabase.co", supabase_key=key
    )
    assert settings.supabase_server_key_configured is expected
    if not expected:
        assert check_backend(settings)["status"] == "incomplete"


def test_legacy_service_role_format():
    def settings(role):
        payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
        return Settings(
            _env_file=None,
            supabase_url="https://example.supabase.co",
            supabase_key=f"header.{payload}.signature",
        )

    assert settings("service_role").supabase_server_key_configured
    assert not settings("anon").supabase_server_key_configured


def test_app_rejects_publishable_key_before_network(tmp_path):
    settings = Settings(
        _env_file=None,
        state_db=str(tmp_path / "state.db"),
        supabase_url="https://example.supabase.co",
        supabase_key="sb_publishable_example",
    )
    with pytest.raises(ValueError, match="backend sb_secret_"):
        create_app(settings)


@pytest.mark.parametrize(
    "code,expected",
    [("42501", "SUPABASE_PERMISSION_DENIED"), ("PGRST205", "SUPABASE_SCHEMA_MISSING")],
)
def test_upload_database_errors_are_json(
    model_file, storage, tmp_path, monkeypatch, code, expected
):
    import api.routes.project

    def fail(*args):
        raise APIError(
            {"code": code, "message": "test dependency error", "details": None, "hint": None}
        )

    monkeypatch.setattr(api.routes.project, "create_project", fail)
    app = create_app(
        Settings(_env_file=None),
        backend=SimpleNamespace(client=storage.client),
        memory=ProjectMemory(tmp_path / "state.db"),
    )
    with TestClient(app) as client:
        response = client.post(
            "/projects",
            data={"name": "Test"},
            files={"file": ("model.ifc", model_file.path.read_bytes())},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == expected


def test_storage_error_is_not_masked_by_failed_cleanup(model_file, storage, tmp_path, monkeypatch):
    import api.routes.project

    def upload_error(*args):
        raise StorageApiError("Bucket not found", "404", 404)

    def cleanup_error(*args):
        raise RuntimeError("Cleanup unavailable")

    monkeypatch.setattr(api.routes.project, "upload_ifc", upload_error)
    monkeypatch.setattr(api.routes.project, "set_file_status", cleanup_error)
    app = create_app(
        Settings(_env_file=None),
        backend=SimpleNamespace(client=storage.client),
        memory=ProjectMemory(tmp_path / "state.db"),
    )
    with TestClient(app) as client:
        response = client.post(
            "/projects",
            data={"name": "Test"},
            files={"file": ("model.ifc", model_file.path.read_bytes())},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SUPABASE_STORAGE_UNAVAILABLE"


def test_readiness_reports_missing_schema_and_bucket(storage):
    settings = Settings(
        _env_file=None, supabase_url="https://example.supabase.co", supabase_key="sb_secret_example"
    )

    def missing_bucket(*args):
        raise StorageApiError("Bucket not found", "404", 404)

    storage.client.storage = SimpleNamespace(get_bucket=missing_bucket)
    result = check_backend(settings, client=storage.client)
    assert result["status"] == "incomplete"
    assert result["checks"][-1]["resource"] == "ifc-files"
    assert result["checks"][-1]["status"] == "failed"
