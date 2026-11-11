import hashlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import ifcopenshell.api.group

from agent.orchestrator import apply_approved_placement_plan
from database.placement_application import approve_placement_plan, get_project_ifc_path, register_project_ifc
from database.project_store import ProjectStore
from ifc_engine.writer import open_model
from ifc_fixture import make_ifc_fixture
from tools.draft_save_placements import draft_save_placements
from tools.ifc_remove_device import ifc_remove_device


def validation(**overrides):
    return {"valid": True, "errors": [], "warnings": [], "suggested_fixes": [], **overrides}


class RemoveDeviceTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = self.temp / "state.db"
        self.enterContext(patch.dict(os.environ, {"MVP_PROJECT_DB_PATH": str(self.db)}))
        self.store = ProjectStore()
        self.a = {"proposed_id": "A", "device_type": "socket", "space_id": "room", "position": [1, 1, 1]}
        self.b = {**self.a, "proposed_id": "B", "position": [2, 1, 1]}
        self.plan = draft_save_placements("p", [self.a, self.b], validation())["plan_id"]
        self.validator = Mock(return_value=validation(warnings=["review quantity"]))

    def remove(self, device_id="A", reason="Bỏ ổ cắm dư", **overrides):
        options = dict(project_id="p", plan_id=self.plan, expected_version=1,
                       validate_placements=self.validator, placement_context={}, rules=[])
        options.update(overrides)
        return ifc_remove_device(device_id, reason, **options)

    def latest(self, plan=None):
        return self.store.get_placement_plan("p", plan or self.plan)

    def make_applied_plan(self):
        source = self.temp / "original.ifc"
        room, host = make_ifc_fixture(source)
        register_project_ifc("p", source)
        proposals = [{**item, "space_id": room, "host_id": host} for item in (self.a, self.b)]
        plan = draft_save_placements("p", proposals, validation())["plan_id"]
        approve_placement_plan("p", plan, 1, reviewer_id="engineer")
        result = apply_approved_placement_plan(plan, 1, "p")
        self.assertEqual(result["status"], "applied", result)
        return source, plan, result["created_ids"]

    def add_external_group(self, device_id):
        # Simulate a subsequent IFC workflow that adds an external reference.
        path = get_project_ifc_path("p")
        model = open_model(path)
        group = ifcopenshell.api.group.add_group(model, name="Circuit group")
        ifcopenshell.api.group.assign_group(model, products=[model.by_guid(device_id)], group=group)
        model.write(str(path))
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "UPDATE project_ifc_versions SET sha256 = ? WHERE project_id = 'p' AND version = 1",
                (hashlib.sha256(path.read_bytes()).hexdigest(),),
            )
        return model.by_type("IfcRelAssignsToGroup")[0].GlobalId

    def test_draft_removal_keeps_history_and_records_reason(self):
        result = self.remove(reason="  Bỏ ổ cắm dư  ")
        self.assertEqual(result["status"], "pending_review", result)
        self.assertEqual(result["removed_id"], "A")
        self.assertEqual(result["version"], 2)
        self.assertEqual(result["affected_references"], [])
        self.validator.assert_called_once_with(proposals=[self.b], placement_context={}, rules=[])
        self.assertEqual(self.latest()["proposals"], [self.b])
        self.assertEqual(self.latest()["diff_summary"]["removed"], ["A"])
        self.assertEqual(self.store.get_placement_plan("p", self.plan, 1)["proposals"], [self.a, self.b])
        record = self.store.get_placement_removal("p", self.plan, 2)
        self.assertEqual(record["reason"], "Bỏ ổ cắm dư")
        self.assertEqual(record["proposed_id"], "A")
        self.assertEqual(record["target"], "draft")
        self.assertIsNone(self.store.get_placement_removal("foreign", self.plan, 2))

    def test_repeat_and_wrong_scope_do_not_create_revisions(self):
        for device_id, reason, options in (
            ("missing", "obsolete", {}), ("A", " ", {}), ("A", "obsolete", {"target": "unknown"}),
            ("A", "obsolete", {"project_id": "foreign"}), ("A", "obsolete", {"target": "ifc"}),
        ):
            with self.subTest(device_id=device_id, reason=reason, options=options):
                self.assertEqual(self.remove(device_id, reason, **options)["status"], "failed")
        self.validator.assert_not_called()
        self.assertEqual(self.remove()["status"], "pending_review")
        self.assertEqual(self.remove()["status"], "conflict")
        self.assertEqual(self.remove(expected_version=2)["status"], "failed")
        self.assertEqual(self.latest()["version"], 2)
        self.assertIsNone(self.store.get_placement_removal("p", self.plan, 3))

    def test_validator_rejects_removal_and_missing_validator_fails_closed(self):
        feedback = validation(valid=False, errors=[{"code": "minimum_quantity"}])
        self.validator.return_value = feedback
        result = self.remove()
        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(result["validation_result"], feedback)
        self.assertEqual(self.remove(validate_placements=None)["status"], "validation_unavailable")
        self.assertEqual(self.latest()["version"], 1)
        self.assertIsNone(self.store.get_placement_removal("p", self.plan, 2))

    def test_last_device_can_be_removed_if_validator_approves(self):
        plan = draft_save_placements("p", [self.a], validation())["plan_id"]
        result = self.remove(plan_id=plan)
        self.assertEqual(result["status"], "pending_review", result)
        self.validator.assert_called_once_with(proposals=[], placement_context={}, rules=[])
        self.assertEqual(self.latest(plan)["proposals"], [])

    def test_concurrent_save_does_not_leave_removal_audit(self):
        def competing_save(**_):
            draft_save_placements("p", [self.a, {**self.b, "reason": "other edit"}], validation(),
                                  plan_id=self.plan, expected_version=1)
            return validation()

        self.assertEqual(self.remove(validate_placements=competing_save)["status"], "conflict")
        self.assertEqual(self.latest()["proposals"][0], self.a)
        self.assertIsNone(self.store.get_placement_removal("p", self.plan, 2))

    def test_audit_failure_rolls_back_new_draft(self):
        seed = draft_save_placements("p", [self.a], validation())["plan_id"]
        self.assertEqual(self.remove(plan_id=seed)["status"], "pending_review")
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("""
                CREATE TRIGGER fail_audit AFTER INSERT ON placement_removals
                BEGIN SELECT RAISE(FAIL, 'audit write failed'); END
            """)
        result = self.remove()
        self.assertEqual(result["status"], "failed")
        self.assertIn("audit write failed", result["errors"][0])
        self.assertEqual(self.latest()["version"], 1)
        self.assertIsNone(self.store.get_placement_removal("p", self.plan, 2))

    def test_applied_removal_requires_approval_and_preserves_other_device(self):
        source, plan, (a, b) = self.make_applied_plan()
        original = source.read_bytes()
        prior = get_project_ifc_path("p")
        before = prior.read_bytes()
        result = self.remove(a, plan_id=plan, target="ifc")
        self.assertEqual(result["status"], "pending_review", result)
        self.assertEqual(result["removed_id"], a)
        self.assertIn("IfcRelConnectsElements", [ref["ifc_class"] for ref in result["affected_references"]])
        self.assertTrue(all(not ref["blocks_removal"] for ref in result["affected_references"]))
        self.assertEqual(self.store.get_placement_removal("p", plan, 2)["affected_references"],
                         result["affected_references"])
        self.assertEqual(apply_approved_placement_plan(plan, 2, "p")["status"], "failed")
        self.assertEqual(get_project_ifc_path("p"), prior)

        approve_placement_plan("p", plan, 2, reviewer_id="engineer")
        applied = apply_approved_placement_plan(plan, 2, "p")
        self.assertEqual(applied["status"], "applied", applied)
        self.assertEqual(applied["model_version"], 2)
        self.assertEqual(applied["removed_ids"], [a])
        output = open_model(get_project_ifc_path("p"))
        self.assertEqual([device.GlobalId for device in output.by_type("IfcOutlet")], [b])
        self.assertEqual(prior.read_bytes(), before)
        self.assertEqual(source.read_bytes(), original)

    def test_external_reference_blocks_by_both_ids_and_is_reported(self):
        _, plan, (a, _) = self.make_applied_plan()
        relationship_id = self.add_external_group(a)
        for device_id, target in ((a, "ifc"), ("A", "draft")):
            result = self.remove(device_id, plan_id=plan, target=target)
            self.assertEqual(result["status"], "blocked", result)
            blockers = [item for item in result["affected_references"] if item["blocks_removal"]]
            self.assertEqual([(ref["ifc_class"], ref["global_id"]) for ref in blockers],
                             [("IfcRelAssignsToGroup", relationship_id)])
        self.validator.assert_not_called()
        self.assertEqual(self.latest(plan)["version"], 1)
        self.assertIsNone(self.store.get_placement_removal("p", plan, 2))

    def test_restore_from_previous_snapshot_through_a_new_approval(self):
        _, plan, (a, b) = self.make_applied_plan()
        removal = self.remove(a, plan_id=plan, target="ifc")
        self.assertEqual(removal["status"], "pending_review", removal)
        approve_placement_plan("p", plan, 2, reviewer_id="engineer")
        self.assertEqual(apply_approved_placement_plan(plan, 2, "p")["removed_ids"], [a])

        previous = self.store.get_placement_plan("p", plan, 1)
        restored = draft_save_placements(
            "p", previous["proposals"], validation(), plan_id=plan, expected_version=2,
        )
        self.assertEqual(restored["version"], 3)
        approve_placement_plan("p", plan, 3, reviewer_id="engineer")
        result = apply_approved_placement_plan(plan, 3, "p")
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["created_ids"], [a])
        model = open_model(get_project_ifc_path("p"))
        self.assertCountEqual([device.GlobalId for device in model.by_type("IfcOutlet")], [a, b])
        self.assertIsNotNone(self.store.get_placement_removal("p", plan, 2))

    def test_late_external_reference_is_rechecked_during_apply(self):
        _, plan, (a, _) = self.make_applied_plan()
        self.assertEqual(self.remove(a, plan_id=plan, target="ifc")["status"], "pending_review")
        self.add_external_group(a)
        prior = get_project_ifc_path("p")
        approve_placement_plan("p", plan, 2, reviewer_id="engineer")
        applied = apply_approved_placement_plan(plan, 2, "p")
        self.assertEqual(applied["status"], "failed")
        self.assertIn("IfcRelAssignsToGroup", applied["errors"][0])
        self.assertEqual(get_project_ifc_path("p"), prior)
        self.assertIsNotNone(open_model(prior).by_guid(a))
