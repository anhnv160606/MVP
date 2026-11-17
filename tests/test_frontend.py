import base64
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from api.frontend import create_preview_app
from config.settings import Settings
from memory.project import ProjectMemory


def test_standalone_preview_needs_no_backend_and_cannot_write():
    with TestClient(create_preview_app()) as client:
        assert client.get("/").status_code == 200
        assert "Volt Studio" in client.get("/").text
        assert client.get("/demo-assets/app.js").status_code == 200
        assert client.get("/demo-assets/styles.css").status_code == 200
        assert client.get("/demo/status").json()["backend_available"] is False
        assert client.post("/projects").status_code == 404


def test_preview_cli_ignores_missing_env_file(monkeypatch, tmp_path):
    import uvicorn

    from electrical_design.cli import main

    launched = {}

    def capture(app, **kwargs):
        launched.update(kwargs)
        launched["title"] = app.title

    monkeypatch.setattr(uvicorn, "run", capture)
    assert main(["preview", "--port", "8001", "--env-file", str(tmp_path / "missing.env")]) == 0
    assert launched["port"] == 8001
    assert launched["title"] == "Electrical design UI preview"


def test_ui_shell_is_public_but_model_and_status_stay_authenticated(model_file, tmp_path):
    memory = ProjectMemory(tmp_path / "state.db")
    project = str(uuid4())
    memory.create(project, ifc_path=str(model_file.path), plan=None)
    app = create_app(
        Settings(_env_file=None, api_token="demo-app-token"),
        backend=SimpleNamespace(),
        memory=memory,
    )
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/demo-assets/app.js").status_code == 200
        assert client.get("/demo/status").status_code == 401
        path = f"/projects/{project}/spaces/{model_file.room}/geometry"
        assert client.get(path).status_code == 401
        client.headers["X-API-Key"] = "demo-app-token"
        status = client.get("/demo/status").json()
        assert status["backend_available"] is True
        assert "supabase_key" not in status
        geometry = client.get(path).json()
        assert geometry["bbox"]["max"] == pytest.approx([4, 3, 3])
        assert client.get(f"/projects/missing/spaces/{model_file.room}/geometry").status_code == 404


def test_browser_websocket_token_subprotocol_is_authenticated(tmp_path):
    class Backend:
        def load_context(self, state):
            return {"status": "not_submitted"}

    memory = ProjectMemory(tmp_path / "state.db")
    project = str(uuid4())
    memory.create(project, plan=None)
    app = create_app(
        Settings(_env_file=None, api_token="test-token"), backend=Backend(), memory=memory
    )
    encoded = base64.urlsafe_b64encode(b"test-token").decode().rstrip("=")
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/projects/{project}/agent/ws", subprotocols=["volt-demo", f"volt-token.{encoded}"]
        ) as socket:
            assert socket.accepted_subprotocol == "volt-demo"
            socket.send_json({"message": "Test"})
            assert socket.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/projects/{project}/agent/ws", subprotocols=["volt-demo", "volt-token.invalid"]
            ):
                pass
