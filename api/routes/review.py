"""Human review gates. These endpoints are never exposed as agent tools."""

from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from tools.ifc_write import get_placement_plan, placement_storage_context


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: UUID
    version: int = Field(ge=1, strict=True)
    reviewer_id: UUID


def create_review_router():
    router = APIRouter(prefix="/projects/{project_id}/review", tags=["review"])

    @router.get("")
    def review(project_id: str, request: Request):
        state = request.app.state.memory.get(project_id)
        plan = state.get("plan")
        with placement_storage_context(request.app.state.backend.client):
            return {
                "state": state,
                "plan": get_placement_plan(project_id, plan["plan_id"], plan["version"])
                if plan
                else None,
            }

    @router.post("/approve")
    async def approve(project_id: str, payload: ApprovalRequest, request: Request):
        return await request.app.state.orchestrator.approve(
            project_id, str(payload.plan_id), payload.version, str(payload.reviewer_id)
        )

    return router
