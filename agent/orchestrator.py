"""Pipeline state machine. The LLM cannot approve or apply its own proposals."""

import asyncio
from enum import StrEnum

from agent.agent import emit
from memory.project import ProjectMemory


class Phase(StrEnum):
    IFC_LOADED = "IFC_LOADED"
    QUESTIONNAIRE = "QUESTIONNAIRE"
    PLACING_DEVICES = "PLACING_DEVICES"
    REVIEW_DEVICES = "REVIEW_DEVICES"
    APPLYING_DEVICES = "APPLYING_DEVICES"
    DEVICES_APPROVED = "DEVICES_APPROVED"


class Orchestrator:
    def __init__(self, memory: ProjectMemory, backend):
        self.memory, self.backend = memory, backend
        self.locks: dict[str, asyncio.Lock] = {}

    def _lock(self, project_id):
        return self.locks.setdefault(project_id, asyncio.Lock())

    def _save(self, state, **changes):
        return self.memory.save({**state, **changes}, state["revision"])

    async def run(self, project_id: str, message: str, *, feedback: str = "", on_event=None):
        async with self._lock(project_id):
            state = self.memory.get(project_id)
            if hasattr(self.backend, "latest_plan"):
                checkpoint = self.backend.latest_plan(project_id)
                if checkpoint:
                    state = {**state, "plan": checkpoint}
            if state["phase"] not in {
                Phase.IFC_LOADED,
                Phase.QUESTIONNAIRE,
                Phase.PLACING_DEVICES,
                Phase.REVIEW_DEVICES,
                Phase.DEVICES_APPROVED,
            }:
                raise ValueError("Cannot start placement in the current phase")
            context = await asyncio.to_thread(
                self.backend.load_context, {**state, "questionnaire_revision": None}
            )
            if context.get("status") != "ok":
                self._save(state, phase=Phase.QUESTIONNAIRE)
                raise ValueError("Submit questionnaire before starting device placement")
            # The questionnaire is pinned to the original architecture, even after electrical edits.
            if context["model_revision"] != state["architecture_revision"]:
                raise ValueError("Questionnaire references a different architecture revision")
            state = self._save(
                state,
                phase=Phase.PLACING_DEVICES,
                questionnaire_revision=context["questionnaire_revision"],
                last_error=None,
            )
            await emit(on_event, {"type": "phase", "phase": state["phase"]})
            try:
                result = await self.backend.run_agent(
                    state, context, message, feedback=feedback, on_event=on_event
                )
                # Only a persisted draft can advance the pipeline, never an LLM assertion.
                if result.get("plan"):
                    state = self._save(state, phase=Phase.REVIEW_DEVICES, plan=result["plan"])
                else:
                    state = self._save(state, phase=Phase.PLACING_DEVICES)
                return {"state": state, "text": result["text"]}
            except BaseException as error:
                # Includes cancellation: preserve a persisted plan for the next session.
                latest_plan = (
                    self.backend.latest_plan(project_id)
                    if hasattr(self.backend, "latest_plan")
                    else getattr(self.backend, "saved_plans", {}).get(project_id)
                )
                self._save(
                    state,
                    phase=Phase.REVIEW_DEVICES if latest_plan else Phase.PLACING_DEVICES,
                    plan=latest_plan or state.get("plan"),
                    last_error=type(error).__name__,
                )
                raise

    async def approve(self, project_id: str, plan_id: str, version: int, reviewer_id: str):
        async with self._lock(project_id):
            state = self.memory.get(project_id)
            if state["phase"] not in {
                Phase.REVIEW_DEVICES,
                Phase.APPLYING_DEVICES,
                Phase.DEVICES_APPROVED,
            }:
                raise ValueError("There is no device proposal awaiting review")
            if (
                state.get("plan", {}).get("plan_id") != plan_id
                or state["plan"]["version"] != version
            ):
                raise ValueError("Approve the exact current plan/version")
            latest = await asyncio.to_thread(
                self.backend.load_context, {**state, "questionnaire_revision": None}
            )
            if latest.get("questionnaire_revision") != state["questionnaire_revision"]:
                raise ValueError(
                    "Questionnaire changed; regenerate the placement draft before approval"
                )
            # Persist before the external side effect. Retrying after a crash is idempotent.
            state = self._save(state, phase=Phase.APPLYING_DEVICES)
            try:
                result = await asyncio.to_thread(self.backend.approve_and_apply, state, reviewer_id)
                if result["status"] not in {"applied", "already_applied"}:
                    raise ValueError(
                        "; ".join(result.get("errors", [])) or "IFC application failed"
                    )
                updates = await asyncio.to_thread(
                    self.backend.refresh_model, state, result["model_version"]
                )
                return self._save(state, phase=Phase.DEVICES_APPROVED, last_error=None, **updates)
            except Exception as error:
                self._save(state, phase=Phase.APPLYING_DEVICES, last_error=type(error).__name__)
                raise
