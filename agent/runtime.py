"""Bind tools, storage and providers to a single project's current IFC revision."""

import asyncio
from contextlib import contextmanager
from pathlib import Path

from agent.agent import Agent
from agent.context_builder import build_device_context
from config.settings import Settings
from database.project_store import snapshot_from_dict
from ifc_engine.reader import read_spaces
from llm.factory import create_llm
from memory.project import ProjectMemory
from memory.session import SessionMemory
from tools import ifc_write, playbook
from tools.calc_preliminary_load import calc_preliminary_load, read_load_catalog
from tools.ifc_inspect import IfcInspection
from tools.ifc_read import (
    ifc_get_placement_context,
    ifc_list_placed_devices,
    playbook_client_context,
)
from tools.phase1.questionnaire_get_load_context import questionnaire_get_load_context
from tools.registry import ToolRegistry

class PlacementBackend:
    def __init__(self, settings: Settings, client, questionnaire_store, memory: ProjectMemory):
        self.settings, self.client, self.questionnaires = settings, client, questionnaire_store
        self.sessions = SessionMemory(memory)
        self.memory = memory
        self.saved_plans: dict[str, dict] = {}

    def load_context(self, state):
        return questionnaire_get_load_context(
            state["project_id"],
            questionnaire_revision=state.get("questionnaire_revision"),
            store=self.questionnaires,
        )

    def registry(self, state: dict, context: dict) -> ToolRegistry:
        @contextmanager
        def storage_context():
            with (
                ifc_write.placement_storage_context(self.client),
                playbook_client_context(self.client),
            ):
                yield

        registry = ToolRegistry(context_factory=storage_context)
        path, project_id = state["ifc_path"], state["project_id"]
        inspection = IfcInspection(path)
        for name in dir(inspection):
            if name.startswith("ifc_"):
                registry.add(getattr(inspection, name))
        registry.add(ifc_get_placement_context, bound={"ifc_file_path": path})
        registry.add(ifc_list_placed_devices, bound={"ifc_file_path": path})
        for name in playbook.__all__:
            registry.add(getattr(playbook, name))

        def requirements(space_id: str | None = None) -> dict:
            """Read questionnaire requirements pinned to this agent run's revision. Optional space_id is a room GlobalId; includes unknowns and unallocated loads."""
            return questionnaire_get_load_context(
                project_id, space_id, context["questionnaire_revision"], store=self.questionnaires
            )

        def validate(proposals: list[dict], rules: dict | None = None) -> dict:
            """Check proposed world-metre positions against this project's current IFC. orientation is yaw degrees. Returns valid/errors/warnings; bbox checks require engineer review."""
            previous = state.get("plan")
            applied = (
                ifc_write._applied_row(self.client, project_id, previous["plan_id"])
                if previous
                else None
            )
            managed = (
                {
                    spec.guid
                    for spec in ifc_write._specs(
                        project_id, previous["plan_id"], applied["proposals"]
                    ).values()
                }
                if applied
                else set()
            )
            return ifc_write._run_validator(
                proposals, Path(path), rules or {}, managed, removing=bool(applied)
            )

        def save(proposals: list[dict]) -> dict:
            """Save the COMPLETE placement proposal as the next durable pending-review revision. Backend recomputes validation; no approval/IFC write occurs. Omitted previous proposals request removal. Keep proposed_id stable."""
            validation = validate(proposals)
            for proposal in proposals:
                # Check the writer contract before an engineer spends time reviewing.
                from ifc_engine.writer import device_spec, resolve_device_mount

                resolve_device_mount(
                    inspection.reader.model, device_spec(project_id, "draft", proposal)
                )
            previous = self.saved_plans.get(project_id) or state.get("plan")
            result = ifc_write.draft_save_placements(
                project_id,
                proposals,
                validation,
                plan_id=previous["plan_id"] if previous else None,
                expected_version=previous["version"] if previous else None,
                source_file_id=state["source_file_id"],
            )
            self.saved_plans[project_id] = result
            self.memory.checkpoint(project_id, result)
            return {**result, "validation_result": validation}

        def modify(device_id: str, changes: dict) -> dict:
            """Stage device edits into a NEW draft revision and revalidate. device_id is the draft UUID, not IFC GlobalId. Changes do not modify the approved IFC until review."""
            return edit(device_id, changes=changes)

        def remove(device_id: str, reason: str) -> dict:
            """Stage removal of one draft device UUID with a reason. Referenced IFC devices may block removal; only an approved revision can remove the model object."""
            return edit(device_id, reason=reason)

        def edit(device_id, *, changes=None, reason=None):
            plan = self.saved_plans.get(project_id) or state.get("plan")
            if not plan:
                raise ValueError("Save a draft first")
            with ifc_write.placement_review_context(project_id, plan["plan_id"], plan["version"]):
                result = (
                    ifc_write.ifc_modify_device(device_id, changes)
                    if changes is not None
                    else ifc_write.ifc_remove_device(device_id, reason)
                ) 
            if result["status"] == "pending_review":
                self.saved_plans[project_id] = {**plan, "version": result["version"]}
                self.memory.checkpoint(project_id, self.saved_plans[project_id])
            return result

        def current_plan(offset: int = 0, limit: int = 10) -> dict:
            """Page the current draft with stable device UUIDs, coordinates, validation and version. Read every page before saving a revised COMPLETE plan; use device UUIDs for edit tools."""
            plan = self.saved_plans.get(project_id) or state.get("plan")
            if (
                type(offset) is not int
                or offset < 0
                or type(limit) is not int
                or not 1 <= limit <= 50
            ):
                raise ValueError("offset must be nonnegative; limit must be 1..50")
            if not plan:
                return {"status": "no_draft"}
            row = ifc_write.get_placement_plan(project_id, plan["plan_id"], plan["version"])
            proposals = row["proposals"]
            return {
                **row,
                "proposals": proposals[offset : offset + limit],
                "total": len(proposals),
                "offset": offset,
            }

        def load(scope_type: str, scope_id: str, snapshot: dict) -> dict:
            """Calculate preliminary room/floor/zone load from a revision-pinned snapshot with placements/requirements. Include load_id, room_id, load_class_id, kind and sourced input_W/VA; socket ratings are not consumption. Unknowns remain explicit."""
            if not self.settings.playbook_path:
                raise ValueError("Configure PLAYBOOK_PATH for preliminary loads")
            model = read_spaces(path)
            data = snapshot_from_dict(snapshot, model)
            return calc_preliminary_load(
                data.plan_id,
                data.plan_revision,
                scope_type,
                scope_id,
                snapshot=data,
                playbook=read_load_catalog(self.settings.playbook_path),
            )

        registry.add(requirements, name="questionnaire_get_load_context")
        registry.add(validate, name="engine_validate_placements")
        registry.add(save, name="draft_save_placements")
        registry.add(current_plan, name="draft_get_placements")
        registry.add(modify, name="ifc_modify_device")
        registry.add(remove, name="ifc_remove_device")
        registry.add(load, name="calc_preliminary_load")
        if self.settings.enable_ifcmcp:
            from tools.ifcmcp_read import READ_TOOLS, IfcMCPReader

            reader = IfcMCPReader(path)
            for name in sorted(READ_TOOLS):
                registry.add(
                    getattr(reader.session, name),
                    description=(
                        "Read-only IfcMCP query on the current project's pinned IFC. STEP IDs are model-local; use GlobalIds for placements. "
                        + (getattr(reader.session, name).__doc__ or name)
                    ),
                )
        return registry

    def latest_plan(self, project_id):
        return self.memory.checkpoint(project_id)

    async def run_agent(self, state, context, message, *, feedback="", on_event=None):
        project_id = state["project_id"]
        self.saved_plans.pop(project_id, None)
        prompt = await asyncio.to_thread(build_device_context, state, context, feedback)
        llm = create_llm(system_prompt=prompt, settings=self.settings)
        try:
            registry = await asyncio.to_thread(self.registry, state, context)
            llm.load_history(self.sessions.history(project_id))
            self.sessions.append(project_id, "user", message)
            text = await Agent(llm, registry, self.settings.max_agent_rounds).run(
                message, on_event=on_event
            )
            self.sessions.append(project_id, "assistant", text)
            return {"text": text, "plan": self.saved_plans.get(project_id)}
        finally:
            await llm.close()

    def approve_and_apply(self, state, reviewer_id):
        plan = state["plan"]
        with ifc_write.placement_storage_context(self.client):
            ifc_write.approve_placement_plan(
                state["project_id"], plan["plan_id"], plan["version"], reviewer_id=reviewer_id
            )
            return ifc_write.ifc_apply_placements_batch(
                plan["plan_id"], plan["version"], state["project_id"]
            )

    def refresh_model(self, state, file_id):
        row = ifc_write._ready_file(self.client, state["project_id"], file_id, "electrical")
        path = Path(self.settings.workspace_dir).resolve() / state["project_id"] / f"{file_id}.ifc"
        path.parent.mkdir(parents=True, exist_ok=True)
        ifc_write._download_model(self.client, row, path)
        return {
            "ifc_path": str(path),
            "source_file_id": file_id,
            "model_revision": read_spaces(path).revision,
        }
