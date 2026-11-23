"""REST entry point for device placement and engineer revision requests."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=20000)
    feedback: str = Field(default="", max_length=20000)


def create_agent_router():
    router = APIRouter(prefix="/projects/{project_id}/agent", tags=["agent"])

    @router.post("/run")
    async def run(project_id: str, payload: AgentRequest, request: Request):
        return await request.app.state.orchestrator.run(
            project_id, payload.message, feedback=payload.feedback
        )

    return router
