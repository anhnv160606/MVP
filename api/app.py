"""FastAPI application factory for a single engineering workspace."""

import hmac
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import HTTPError
from postgrest.exceptions import APIError
from storage3.exceptions import StorageException

from agent.orchestrator import Orchestrator
from agent.runtime import PlacementBackend
from api.frontend import attach_frontend
from api.routes.agent import create_agent_router
from api.routes.project import create_project_router
from api.routes.questionnaire import create_questionnaire_router
from api.routes.review import create_review_router
from api.websocket import create_websocket_router
from config.settings import Settings
from database.supabase_client import get_supabase_client
from ifc_engine.reader import read_spaces
from memory.project import ProjectMemory, StateConflict
from tools.ifc_write import PlacementConflict, PlacementDatabaseError
from tools.phase1.questionnaire_submit import QuestionnaireStore

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, *, backend=None, memory=None, panel_service_factory=None
) -> FastAPI:
    settings = settings or Settings()
    if settings.app_env == "prod" and settings.api_token is None:
        raise ValueError("API_TOKEN is required in production")
    memory = memory or ProjectMemory(settings.state_db)
    if backend is None:
        if not settings.supabase_server_key_configured:
            raise ValueError(
                "SUPABASE_KEY must be a backend sb_secret_ key or legacy service_role key; publishable/anon keys cannot run this backend."
            )
        client = get_supabase_client(settings)
        backend = PlacementBackend(settings, client, QuestionnaireStore(client), memory)
    app = FastAPI(title="Electrical device placement backend", version="0.2.0")
    app.state.settings, app.state.memory, app.state.backend = settings, memory, backend
    app.state.orchestrator = Orchestrator(memory, backend)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.method in {"GET", "HEAD"} and (
            request.url.path == "/" or request.url.path.startswith("/demo-assets/")
        ):
            return await call_next(request)
        if settings.api_token and not hmac.compare_digest(
            request.headers.get("x-api-key", ""), settings.api_token.get_secret_value()
        ):
            return JSONResponse({"detail": "Invalid API key"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        return JSONResponse({"detail": "Project or resource not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        status = 409 if isinstance(error, (StateConflict, PlacementConflict)) else 422
        return JSONResponse({"detail": str(error)}, status_code=status)

    @app.exception_handler(PlacementDatabaseError)
    async def storage_failure(request, error):
        return JSONResponse(
            {"detail": "Project storage unavailable; inspect backend logs"}, status_code=503
        )

    @app.exception_handler(APIError)
    async def database_error(request, error):
        log.error("Supabase database request failed: code=%s", error.code)
        if error.code in {"42P01", "PGRST205", "42703", "PGRST204"}:
            detail = {
                "code": "SUPABASE_SCHEMA_MISSING",
                "message": "Apply the missing project/questionnaire/placement migrations on this Supabase project.",
            }
        elif error.code == "42501":
            detail = {
                "code": "SUPABASE_PERMISSION_DENIED",
                "message": "Use a backend secret/service_role SUPABASE_KEY and verify migration grants.",
            }
        else:
            detail = {
                "code": "SUPABASE_DATABASE_ERROR",
                "message": "Database request failed; inspect backend logs and migration constraints.",
            }
        return JSONResponse({"detail": {**detail, "database_code": error.code}}, status_code=503)

    @app.exception_handler(StorageException)
    async def storage_api_error(request, error):
        log.error("Supabase storage request failed: code=%s", getattr(error, "code", "unknown"))
        return JSONResponse(
            {
                "detail": {
                    "code": "SUPABASE_STORAGE_UNAVAILABLE",
                    "message": "Verify the private ifc-files bucket exists and SUPABASE_KEY is a backend secret/service_role key.",
                }
            },
            status_code=503,
        )

    @app.exception_handler(HTTPError)
    async def network_error(request, error):
        log.error("Backend network dependency failed: %s", type(error).__name__)
        return JSONResponse(
            {
                "detail": {
                    "code": "BACKEND_NETWORK_ERROR",
                    "message": "Cannot reach a backend service; check network connectivity.",
                }
            },
            status_code=503,
        )

    @app.get("/health")
    def health():
        return {"status": "ok", "scope": "device_placement"}

    def resolve_model(project_id):
        try:
            state = memory.get(project_id)
        except KeyError:
            return None
        # Phase 1 stays attached to architecture, not generated electrical snapshots.
        return read_spaces(state["architecture_path"])

    app.include_router(create_project_router())
    app.include_router(create_agent_router())
    app.include_router(create_review_router())
    if panel_service_factory is not None:
        from api.routes.panels import create_panel_router

        app.include_router(create_panel_router(panel_service_factory))
    app.include_router(create_websocket_router())
    if hasattr(backend, "questionnaires"):
        from pathlib import Path

        app.include_router(
            create_questionnaire_router(
                backend.questionnaires,
                resolve_model,
                Path(settings.playbook_path) if settings.playbook_path else None,
            )
        )
    attach_frontend(app)
    return app
