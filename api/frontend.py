"""Serve the local demo UI and project-scoped, read-only display endpoints."""

from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from database.readiness import check_backend
from ifc_engine.reader import IfcReader

WEB_ROOT = Path(__file__).resolve().parents[1] / "electrical_design" / "web"


def attach_frontend(app: FastAPI, *, preview_only: bool = False):
    """Assets are public; project data and diagnostics retain API authentication."""
    app.mount("/demo-assets", StaticFiles(directory=WEB_ROOT), name="demo-assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_ROOT / "index.html")

    router = APIRouter(tags=["demo"])

    @router.get("/demo/status")
    def status(request: Request, check_dependencies: bool = False):
        if preview_only:
            return {
                "backend_available": False,
                "message": "Đang xem giao diện độc lập. Chạy lệnh serve để sử dụng backend thật.",
            }
        settings = request.app.state.settings
        result = {
            "backend_available": True,
            "llm_configured": bool(settings.llm_model),
            "provider": settings.llm_provider,
        }
        if check_dependencies:
            result["dependencies"] = check_backend(
                settings, client=request.app.state.backend.client
            )
        return result

    if not preview_only:

        @router.get("/projects/{project_id}/spaces/{space_id}/geometry")
        def room_geometry(project_id: str, space_id: str, request: Request):
            state = request.app.state.memory.get(project_id)
            reader = IfcReader(Path(state["ifc_path"]))
            return reader.get_space_geometry(space_id)

    app.include_router(router)


def create_preview_app() -> FastAPI:
    """Preview UI without Supabase credentials or any write endpoints."""
    app = FastAPI(title="Electrical design UI preview")
    attach_frontend(app, preview_only=True)
    return app
