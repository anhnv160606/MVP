"""Panel review endpoints, mounted only when the backend supplies a service factory."""

from fastapi import APIRouter, HTTPException

from database.panel_store import PanelConflict
from tools.panel_contracts import ModifyRequest as ModifyRequest
from tools.panel_contracts import SaveRequest as SaveRequest


def create_panel_router(service_factory):
    """service_factory(project_id) verifies access and binds the CURRENT context.
    Existing app authentication remains in force. No approve/apply endpoint is
    provided by Dev C. Callers cannot submit project IDs inside schedule JSON.
    """
    router = APIRouter(prefix="/projects/{project_id}/panels", tags=["panel-review"])

    def execute(project_id, method, **kwargs):
        try:
            return getattr(service_factory(project_id), method)(**kwargs)
        except PanelConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("")
    def read(project_id: str, plan_id: str | None = None, version: int | None = None):
        return execute(project_id, "get_schedule_snapshot", plan_id=plan_id, version=version)

    @router.post("/drafts")
    def save(project_id: str, payload: SaveRequest):
        return execute(project_id, "draft_save_panel_schedule", **payload.model_dump())

    @router.post("/{plan_id}/modify")
    def modify(project_id: str, plan_id: str, payload: ModifyRequest):
        return execute(project_id, "panel_modify_schedule", plan_id=plan_id, **payload.model_dump())

    @router.post("/{plan_id}/versions/{version}/retry-sld")
    def render(project_id: str, plan_id: str, version: int):
        return execute(project_id, "retry_sld", plan_id=plan_id, version=version)

    return router
