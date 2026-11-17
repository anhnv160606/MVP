import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent.agent import Agent, AgentLimitError
from agent.orchestrator import Orchestrator
from agent.runtime import PlacementBackend
from api.app import create_app
from config.settings import Settings
from ifc_engine.reader import read_spaces
from llm.base import LLMResponse, ToolCall
from memory.project import ProjectMemory, StateConflict
from tools.registry import ToolRegistry


class ScriptedLLM:
    def __init__(self, responses):
        self.responses, self.results = iter(responses), []

    def set_tools(self, schemas):
        self.schemas = schemas

    async def generate(self, message=None):
        return next(self.responses)

    def add_tool_results(self, results):
        self.results.extend(results)

    def load_history(self, messages):
        self.history = messages

    async def close(self):
        pass


def test_agent_repairs_invalid_tool_calls_and_never_exposes_writes():
    registry = ToolRegistry()

    def room(space_id: str) -> dict:
        """Read one IFC room by GlobalId, returning only its public metadata."""
        return {"id": space_id}

    registry.add(room)
    llm = ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(id="1", name="ifc_apply_placements_batch", arguments={}),
                    ToolCall(id="2", name="room", arguments={"space_id": 123}),
                ]
            ),
            LLMResponse(tool_calls=[ToolCall(id="3", name="room", arguments={"space_id": "abc"})]),
            LLMResponse(text="Finished"),
        ]
    )
    assert asyncio.run(Agent(llm, registry).run("Place outlets")) == "Finished"
    assert "error" in llm.results[0][1]
    assert "error" in llm.results[1][1]
    assert llm.results[2][1] == {"id": "abc"}
    repeating = ScriptedLLM(
        [LLMResponse(tool_calls=[ToolCall(id="4", name="room", arguments={"space_id": "abc"})])]
    )
    with pytest.raises(AgentLimitError):
        asyncio.run(Agent(repeating, registry, max_rounds=1).run("Run"))


class Backend:
    def __init__(self, revision):
        self.context = {"status": "ok", "model_revision": revision, "questionnaire_revision": 1}
        self.saved_plans = {}
        self.plan = {"plan_id": str(uuid4()), "version": 1}
        self.apply_count = 0

    def load_context(self, state):
        return self.context

    async def run_agent(self, state, context, message, **kwargs):
        return {"text": "Draft ready", "plan": self.plan}

    def approve_and_apply(self, state, reviewer_id):
        self.apply_count += 1
        return {"status": "applied", "model_version": "model-2"}

    def refresh_model(self, state, file_id):
        return {"source_file_id": file_id, "model_revision": "electrical-2"}


def setup_runtime(tmp_path):
    memory = ProjectMemory(tmp_path / "state.sqlite3")
    project_id = str(uuid4())
    memory.create(
        project_id,
        ifc_path="model.ifc",
        architecture_revision="arch-1",
        model_revision="arch-1",
        plan=None,
    )
    return memory, project_id, Backend("arch-1")


def test_orchestrator_durable_state_and_exact_approval(tmp_path):
    memory, project, backend = setup_runtime(tmp_path)
    orch = Orchestrator(memory, backend)

    async def scenario():
        result = await orch.run(project, "Propose placements")
        assert result["state"]["phase"] == "REVIEW_DEVICES"
        reopened = ProjectMemory(memory.path)
        assert reopened.get(project)["plan"] == backend.plan
        with pytest.raises(ValueError):
            await orch.approve(project, backend.plan["plan_id"], 2, str(uuid4()))
        backend.context["questionnaire_revision"] = 2
        with pytest.raises(ValueError, match="Questionnaire changed"):
            await orch.approve(project, backend.plan["plan_id"], 1, str(uuid4()))
        assert backend.apply_count == 0
        await orch.run(project, "Regenerate for changed requirements")
        state = await orch.approve(project, backend.plan["plan_id"], 1, str(uuid4()))
        assert state["phase"] == "DEVICES_APPROVED"

    asyncio.run(scenario())


def test_missing_questionnaire_and_llm_assertion_do_not_advance(tmp_path):
    memory, project, backend = setup_runtime(tmp_path)
    orch = Orchestrator(memory, backend)
    backend.context = {"status": "not_submitted"}
    with pytest.raises(ValueError):
        asyncio.run(orch.run(project, "Run"))
    assert memory.get(project)["phase"] == "QUESTIONNAIRE"
    backend.context = {"status": "ok", "model_revision": "arch-1", "questionnaire_revision": 1}
    backend.plan = None
    result = asyncio.run(orch.run(project, "Run"))
    assert result["state"]["phase"] == "PLACING_DEVICES"


def test_state_compare_and_swap(tmp_path):
    memory, project, _ = setup_runtime(tmp_path)
    state = memory.get(project)
    memory.save(state, state["revision"])
    with pytest.raises(StateConflict):
        memory.save(state, state["revision"])


def test_registry_project_binding_and_draft_validation(model_file, proposal, storage, tmp_path):
    settings = Settings(_env_file=None, state_db=str(tmp_path / "db"))
    memory = ProjectMemory(settings.state_db)
    backend = PlacementBackend(settings, storage.client, None, memory)
    state = {
        "project_id": storage.project_id,
        "ifc_path": str(model_file.path),
        "source_file_id": storage.file_id,
        "plan": None,
    }
    registry = backend.registry(state, {"questionnaire_revision": 1})
    names = set(registry.tools)
    assert {"draft_save_placements", "ifc_modify_device", "engine_validate_placements"} <= names
    assert (
        not {"engine_plan_placements", "ifc_apply_placements_batch", "approve_placement_plan"}
        & names
    )
    with pytest.raises(ValueError):
        registry.execute(
            "ifc_get_placement_context", {"space_id": model_file.room, "ifc_file_path": "other.ifc"}
        )
    saved = registry.execute("draft_save_placements", {"proposals": [proposal]})
    assert saved["validation_result"]["valid"]
    assert registry.execute("draft_get_placements", {})["version"] == 1
    assert registry.execute("draft_save_placements", {"proposals": [proposal]})["version"] == 2


def test_api_and_websocket_auth_and_review(tmp_path):
    memory, project, backend = setup_runtime(tmp_path)
    settings = Settings(_env_file=None, api_token="test-token", app_env="test")
    app = create_app(settings, backend=backend, memory=memory)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 401
        client.headers["x-api-key"] = "test-token"
        assert client.get("/health").status_code == 200
        assert client.get("/projects/missing").status_code == 404
        assert (
            client.post(f"/projects/{project}/agent/run", json={"message": "Run"}).status_code
            == 200
        )
        result = client.post(
            f"/projects/{project}/review/approve",
            json={**backend.plan, "reviewer_id": str(uuid4())},
        )
        assert result.status_code == 200, result.text
        assert result.json()["phase"] == "DEVICES_APPROVED"
        with client.websocket_connect(
            f"/projects/{project}/agent/ws", headers={"x-api-key": "test-token"}
        ) as socket:
            socket.send_json([])
            assert socket.receive_json()["code"] == "INVALID_REQUEST"
            socket.send_json({"message": "Revise outlets"})
            assert socket.receive_json()["type"] == "phase"
            assert socket.receive_json()["type"] == "done"


def test_groq_history_contains_assistant_tool_call():
    from llm.groq_provider import GroqProvider

    async def scenario():
        provider = GroqProvider("test-key", "test-model")
        await provider.client.close()
        call = SimpleNamespace(
            id="call-1", function=SimpleNamespace(name="room", arguments='{"space_id":"x"}')
        )

        async def create(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))]
            )

        provider.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        await provider.generate("Inspect room")
        provider.add_tool_results([("call-1", {"id": "x"})])
        assert [m["role"] for m in provider.messages] == ["user", "assistant", "tool"]
        assert provider.messages[1]["tool_calls"][0]["id"] == "call-1"

    asyncio.run(scenario())


def test_real_backend_from_scripted_agent_through_review(
    model_file, proposal, storage, tmp_path, monkeypatch
):
    """Real runtime/tools/orchestrator/IFC writer; only LLM and remote storage are doubles."""
    import agent.runtime

    model = read_spaces(model_file.path)
    settings = Settings(
        _env_file=None, state_db=str(tmp_path / "state.db"), workspace_dir=str(tmp_path / "models")
    )
    memory = ProjectMemory(settings.state_db)

    class Questionnaires:
        def read(self, project_id, revision=None):
            return {
                "questionnaire_revision": 1,
                "submission": {"model_revision": model.revision, "space_ids": [model_file.room]},
                "context": {"requirements": [], "unresolved_requirements": [], "warnings": []},
            }

    backend = PlacementBackend(settings, storage.client, Questionnaires(), memory)
    memory.create(
        storage.project_id,
        ifc_path=str(model_file.path),
        architecture_revision=model.revision,
        model_revision=model.revision,
        source_file_id=storage.file_id,
        plan=None,
    )
    scripted = ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="save", name="draft_save_placements", arguments={"proposals": [proposal]}
                    )
                ]
            ),
            LLMResponse(text="Review the saved draft"),
        ]
    )
    monkeypatch.setattr(agent.runtime, "create_llm", lambda **kwargs: scripted)
    orch = Orchestrator(memory, backend)

    async def scenario():
        result = await orch.run(storage.project_id, "Place one outlet")
        assert result["state"]["phase"] == "REVIEW_DEVICES"
        plan = result["state"]["plan"]
        assert memory.checkpoint(storage.project_id)["version"] == plan["version"]
        state = await orch.approve(
            storage.project_id, plan["plan_id"], plan["version"], str(uuid4())
        )
        assert state["phase"] == "DEVICES_APPROVED"
        assert state["model_revision"] != model.revision
        assert state["source_file_id"] != storage.file_id

    asyncio.run(scenario())


def test_api_upload_download_and_invalid_ifc(model_file, storage, tmp_path, monkeypatch):
    import api.routes.project

    settings = Settings(_env_file=None, app_env="test", workspace_dir=str(tmp_path / "projects"))
    memory = ProjectMemory(tmp_path / "upload-state.db")
    backend = SimpleNamespace(client=storage.client)
    app = create_app(settings, backend=backend, memory=memory)
    monkeypatch.setattr(
        api.routes.project,
        "upload_ifc",
        lambda c, b, key, path: c.objects.__setitem__(key, path.read_bytes()),
    )
    original = model_file.path.read_bytes()
    with TestClient(app) as client:
        bad = client.post(
            "/projects", data={"name": "Bad IFC"}, files={"file": ("bad.ifc", b"not an IFC")}
        )
        assert bad.status_code == 422
        result = client.post(
            "/projects",
            data={"name": "Upload test"},
            files={"file": ("architecture.ifc", original)},
        )
        assert result.status_code == 201, result.text
        state = result.json()
        assert state["phase"] == "IFC_LOADED"
        downloaded = client.get(f"/projects/{state['project_id']}/ifc")
        assert downloaded.content == original
        assert state["model_revision"] == read_spaces(model_file.path).revision
