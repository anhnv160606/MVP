import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ifcopenshell.util.element
import ifcopenshell.util.placement
import numpy as np

from agent.orchestrator import apply_approved_placement_plan
from database.placement_application import approve_placement_plan, get_project_ifc_path, register_project_ifc
from database.project_store import ProjectStore
from ifc_engine.writer import open_model
from ifc_fixture import make_ifc_fixture
from tools.draft_save_placements import draft_save_placements
from tools.ifc_modify_device import ifc_modify_device


def check(**overrides):
    return {"valid": True, "errors": [], "warnings": [], "suggested_fixes": [], **overrides}


class ModifyDeviceTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"MVP_PROJECT_DB_PATH": str(self.temp / "state.db")}))
        self.store = ProjectStore()
        self.a = {
            "proposed_id": "A", "device_type": "socket", "space_id": "room-1",
            "host_id": "wall-1", "position": [1, 2, 0.4], "orientation": 0,
            "ifc_class": "IfcOutlet", "module_id": "socket-1", "geometry_ref": "asset-1",
            "dimensions_m": [0.08, 0.03, 0.08], "specs": {"voltage": 230},
            "rated_power": 30, "rule_ids": ["socket-rule"], "properties": {"tag": "keep", "label": "old"},
        }
        self.b = {**self.a, "proposed_id": "B", "position": [2, 2, 0.4]}
        self.plan = draft_save_placements("p", [self.a, self.b], check())["plan_id"]
        self.context = {"room-1": {"coordinate_system": "model_metres"}}
        self.rules = [{"rule_id": "socket-rule"}]
        self.validator = Mock(return_value=check(warnings=["review height"]))

    def modify(self, changes, **overrides):
        options = {
            "project_id": "p", "plan_id": self.plan, "expected_version": 1,
            "validate_placements": self.validator, "placement_context": self.context, "rules": self.rules,
            **overrides,
        }
        device_id = options.pop("device_id", "A")
        return ifc_modify_device(device_id, changes, **options)

    def latest(self):
        return self.store.get_placement_plan("p", self.plan)

    def approve(self, plan, version):
        approve_placement_plan("p", plan, version, reviewer_id="engineer")

    def test_edit_validates_entire_candidate_and_preserves_history(self):
        result = self.modify({
            "position": {"x": 3, "y": 2, "z": 0.4}, "orientation": {"yaw": 90},
            "host_id": "wall-2", "properties": {"label": "new"},
        })
        self.assertEqual(result["status"], "pending_review", result)
        self.assertEqual(result["version"], 2)
        updated = result["updated_device"]
        self.assertEqual(updated["proposed_id"], "A")
        self.assertEqual(updated["host_id"], "wall-2")
        self.assertEqual(updated["orientation"], {"yaw": 90})
        self.assertEqual(updated["properties"], {"tag": "keep", "label": "new"})
        self.validator.assert_called_once_with(proposals=[updated, self.b],
                                               placement_context=self.context, rules=self.rules)
        self.assertEqual(self.latest()["validation_result"], check(warnings=["review height"]))
        self.assertEqual(self.latest()["diff_summary"]["updated"], ["A"])
        self.assertEqual(self.store.get_placement_plan("p", self.plan, 1)["proposals"], [self.a, self.b])

    def test_change_type_clears_stale_catalog_metadata(self):
        result = self.modify({"device_type": "light", "module_id": "lamp-1", "rule_ids": ["light-rule"]})
        self.assertEqual(result["status"], "pending_review", result)
        updated = result["updated_device"]
        self.assertEqual(updated["module_id"], "lamp-1")
        self.assertEqual(updated["rule_ids"], ["light-rule"])
        for key in ("ifc_class", "geometry_ref", "dimensions_m", "specs", "rated_power"):
            self.assertNotIn(key, updated)

    def test_change_room_clears_old_host_and_rules(self):
        result = self.modify({"space_id": "room-2"})
        self.assertEqual(result["status"], "pending_review", result)
        self.assertNotIn("host_id", result["updated_device"])
        self.assertNotIn("rule_ids", result["updated_device"])

    def test_invalid_plan_returns_validator_feedback_without_saving(self):
        feedback = check(valid=False, errors=[{"code": "collision"}], suggested_fixes=["Move left"])
        self.validator.return_value = feedback
        result = self.modify({"position": self.b["position"]})
        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(result["validation_result"], feedback)
        self.assertIsNone(result["updated_device"])
        self.assertEqual(self.latest()["version"], 1)

    def test_missing_failed_or_malformed_validator_cannot_save(self):
        for config in (
            {"validate_placements": None}, {"rules": None},
            {"validate_placements": Mock(side_effect=RuntimeError("validator crashed"))},
            {"validate_placements": Mock(return_value={"valid": True})},
            {"validate_placements": Mock(return_value=check(errors=["contradictory"]))},
        ):
            with self.subTest(config=config):
                result = self.modify({"orientation": 20}, **config)
                self.assertEqual(result["status"], "validation_unavailable")
        self.assertEqual(self.latest()["version"], 1)

    def test_rejects_wrong_scope_invalid_device_and_malformed_changes(self):
        for changes, options in (
            ({"orientation": 20}, {"project_id": "foreign"}),
            ({"orientation": 20}, {"device_id": "missing"}),
            ({"orientation": 20}, {"target": "unknown"}),
            ({"proposed_id": "X"}, {}),
            ({"position": [1, 2, float("nan")]}, {}),
            ({"orientation": {"pitch": 90}}, {}),
            ({"dimensions_m": [0.2, -0.01, 0.1]}, {}),
            ({"properties": []}, {}),
        ):
            with self.subTest(changes=changes, options=options):
                self.assertEqual(self.modify(changes, **options)["status"], "failed")
        self.validator.assert_not_called()
        self.assertEqual(self.latest()["version"], 1)

    def test_stale_edit_fails_before_running_validation(self):
        result = self.modify({"orientation": 20}, expected_version=2)
        self.assertEqual(result["status"], "conflict")
        self.validator.assert_not_called()

    def test_concurrent_save_during_validation_cannot_overwrite_revision(self):
        def competing_save(**_):
            draft_save_placements("p", [self.a, {**self.b, "reason": "other edit"}], check(),
                                  plan_id=self.plan, expected_version=1)
            return check()

        result = self.modify({"orientation": 20}, validate_placements=competing_save)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.latest()["version"], 2)
        self.assertEqual(self.latest()["proposals"][0], self.a)
        self.assertEqual(self.latest()["proposals"][1]["reason"], "other edit")

    def test_noop_validates_without_creating_revision(self):
        result = self.modify({"orientation": 0})
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["version"], 1)
        self.assertEqual(self.latest()["version"], 1)
        self.validator.assert_called_once()

    def test_validator_mutation_does_not_modify_saved_data(self):
        def validator(proposals, **_):
            proposals[1]["position"][0] = -999
            return check()

        result = self.modify({"orientation": 20}, validate_placements=validator)
        self.assertEqual(result["status"], "pending_review", result)
        self.assertEqual(self.latest()["proposals"][1], self.b)

    def test_ifc_edit_preserves_pending_changes_and_only_applies_after_approval(self):
        source = self.temp / "source.ifc"
        space, wall = make_ifc_fixture(source)
        register_project_ifc("p", source)
        original = source.read_bytes()
        a = {**self.a, "space_id": space, "host_id": wall, "position": [4.9, -1.0, 3.35]}
        b = {**a, "proposed_id": "B", "position": [4.9, 0.0, 3.35]}
        plan = draft_save_placements("p", [a, b], check())["plan_id"]
        self.approve(plan, 1)
        first = apply_approved_placement_plan(plan, 1, "p")
        self.assertEqual(first["status"], "applied", first)
        device_id = first["created_ids"][0]
        path1 = get_project_ifc_path("p")
        old_ifc = path1.read_bytes()
        pending_b = {**b, "reason": "pending edit to another device"}
        draft_save_placements("p", [a, pending_b], check(), plan_id=plan, expected_version=1)

        result = self.modify({"position": [4.8, -0.5, 3.35], "host_id": None, "orientation": 90},
                             plan_id=plan, expected_version=2, device_id=device_id, target="ifc")
        self.assertEqual(result["status"], "pending_review", result)
        self.assertEqual(result["version"], 3)
        self.assertEqual(self.store.get_placement_plan("p", plan)["proposals"][1], pending_b)
        self.assertEqual(get_project_ifc_path("p"), path1)
        self.assertEqual(apply_approved_placement_plan(plan, 3, "p")["status"], "failed")

        self.approve(plan, 3)
        applied = apply_approved_placement_plan(plan, 3, "p")
        self.assertEqual(applied["status"], "applied", applied)
        self.assertIn(device_id, applied["updated_ids"])
        model = open_model(get_project_ifc_path("p"))
        device = model.by_guid(device_id)
        pose = ifcopenshell.util.placement.get_local_placement(device.ObjectPlacement)
        np.testing.assert_allclose(pose[:3, 3], [4800, -500, 3350], atol=1e-7)
        np.testing.assert_allclose(pose[:3, 0], [0, 1, 0], atol=1e-7)
        self.assertIsNone(ifcopenshell.util.element.get_pset(device, "MVP_Placement", "HostGlobalId"))
        self.assertEqual(path1.read_bytes(), old_ifc)
        self.assertEqual(source.read_bytes(), original)

    def test_ifc_target_rejects_unmanaged_or_removed_device(self):
        source = self.temp / "source.ifc"
        space, wall = make_ifc_fixture(source)
        register_project_ifc("p", source)
        plan = draft_save_placements("p", [{**self.a, "space_id": space, "host_id": wall}], check())["plan_id"]
        self.approve(plan, 1)
        first = apply_approved_placement_plan(plan, 1, "p")
        self.assertEqual(first["status"], "applied", first)
        unmanaged = self.modify({"orientation": 90}, plan_id=plan, device_id=wall, target="ifc")
        self.assertEqual(unmanaged["status"], "failed")
        draft_save_placements("p", [], check(), plan_id=plan, expected_version=1)
        removed = self.modify({"orientation": 90}, plan_id=plan, expected_version=2,
                              device_id=first["created_ids"][0], target="ifc")
        self.assertEqual(removed["status"], "failed")
        self.validator.assert_not_called()
