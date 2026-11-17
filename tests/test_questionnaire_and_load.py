from copy import deepcopy

import pytest

from database.project_store import snapshot_from_dict
from ifc_engine.reader import read_spaces
from tools.calc_preliminary_load import LoadClass, Playbook, calc_preliminary_load
from tools.phase1.questionnaire_get_load_context import (
    _derive_context,
    questionnaire_get_load_context,
)
from tools.phase1.questionnaire_get_template import questionnaire_get_template
from tools.phase1.questionnaire_submit import QuestionnaireRevisionConflict, questionnaire_submit


class Store:
    def __init__(self):
        self.rows = {}

    def read(self, project, revision=None):
        rows = self.rows.get(project, [])
        if revision is None:
            return deepcopy(rows[-1]) if rows else None
        return next((deepcopy(r) for r in rows if r["questionnaire_revision"] == revision), None)

    def save(self, project, expected, submission, build_context):
        rows = self.rows.setdefault(project, [])
        if len(rows) != expected:
            raise QuestionnaireRevisionConflict(expected, len(rows))
        previous = rows[-1]["context"]["requirement_ids"] if rows else {}
        result = {
            "questionnaire_revision": expected + 1,
            "submission": submission,
            "context": build_context(previous),
        }
        rows.append(deepcopy(result))
        return result


def test_template_submit_revision_and_requirement_identity(model_file):
    model, store = read_spaces(model_file.path), Store()
    template = questionnaire_get_template("project", "townhouse", model.revision, model=model)
    assert template["spaces"][0]["space_id"] == model_file.room
    answer = {
        "answer_item_id": "ac-1",
        "q_id": "air_conditioning",
        "selected": "needed",
        "space_id": model_file.room,
        "quantity": 1,
        "input_W": 900,
        "power_source": "nameplate",
    }

    def submit(revision, answers):
        return questionnaire_submit(
            "project",
            template["template_id"],
            template["template_version"],
            model.revision,
            revision,
            answers,
            model=model,
            store=store,
        )

    assert questionnaire_get_load_context("project", store=store)["status"] == "not_submitted"
    submit(0, [answer])
    first = questionnaire_get_load_context("project", questionnaire_revision=1, store=store)
    submit(1, [{**answer, "quantity": 2}])
    latest = questionnaire_get_load_context("project", store=store)
    assert latest["requirements"][0]["requirement_id"] == first["requirements"][0]["requirement_id"]
    assert latest["requirements"][0]["quantity"] == 2
    with pytest.raises(QuestionnaireRevisionConflict):
        submit(1, [answer])
    with pytest.raises(ValueError):
        submit(2, [{**answer, "space_id": "invalid-room"}])
    with pytest.raises(ValueError):
        submit(2, [{**answer, "quantity": True}])
    unknown = _derive_context([{**answer, "space_id": None, "quantity": None, "input_W": None}], {})
    assert unknown["unresolved_requirements"]
    assert {w["code"] for w in unknown["warnings"]} >= {"UNKNOWN_QUANTITY", "UNKNOWN_INPUT_POWER"}


def test_load_no_double_count_and_unknown_power(model_file):
    model = read_spaces(model_file.path)
    playbook = Playbook(
        "catalog-1",
        {"light": LoadClass("light", "Lighting", 1, "full_load_scenario", "project rule")},
    )
    requirement = {
        "requirement_id": "req-1",
        "room_id": model_file.room,
        "load_class_id": "light",
        "quantity": 1,
        "input_W": 100,
        "power_source": "nameplate",
    }
    placement = {
        "load_id": "load-1",
        "room_id": model_file.room,
        "load_class_id": "light",
        "kind": "equipment",
        "requirement_id": "req-1",
        "input_W": 100,
        "power_source": "nameplate",
        "input_VA": 125,
        "power_factor": 0.8,
        "pf_source": "nameplate",
    }
    data = {
        "plan_id": "plan",
        "plan_revision": "1",
        "placements": [placement],
        "requirements": [requirement],
    }
    snapshot = snapshot_from_dict(data, model)
    result = calc_preliminary_load(
        "plan", "1", "room", model_file.room, snapshot=snapshot, playbook=playbook
    )
    assert result["connected_load_W"] == 100
    assert result["estimated_demand_W"] == 100
    unknown = {**placement, "input_W": None, "input_VA": None, "power_factor": None}
    snapshot = snapshot_from_dict(
        {**data, "placements": [unknown], "requirements": [{**requirement, "input_W": None}]}, model
    )
    result = calc_preliminary_load(
        "plan", "1", "floor", model_file.floor, snapshot=snapshot, playbook=playbook
    )
    assert result["connected_load_W"] is None
    assert any(w["code"] == "MISSING_INPUT_POWER" for w in result["warnings"])
