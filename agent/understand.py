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
            store=self.questionnaires, #store lấy thông tin câu trả lời từ supabase
        )

    def registry(self, state: dict, context: dict) -> ToolRegistry:
        @contextmanager
        def storage_context():
            with (ifc_write.placement_storage_context(self.client),
                  playbook_client_context(self.client)):
                yield
        
            
