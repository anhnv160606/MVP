import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import ifcopenshell.api.group
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.validate
import numpy as np

from agent.orchestrator import apply_approved_placement_plan
from database.placement_application import (
    PlacementApplicationError,
    approve_placement_plan,
    get_project_ifc_path,
    register_project_ifc,
)
from ifc_engine.writer import open_model
from ifc_fixture import make_ifc_fixture
from tools.draft_save_placements import draft_save_placements


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ApplyPlacementsTest(unittest.TestCase):
    def setUp(self):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db_path = temp / "project.db"
        self.enterContext(patch.dict(os.environ, {"MVP_PROJECT_DB_PATH": str(self.db_path)}))
        self.source = temp / "architecture.ifc"
        self.space_id, self.wall_id = make_ifc_fixture(self.source)
        self.source_digest = digest(self.source)
        register_project_ifc("p1", self.source)
        self.baseline = get_project_ifc_path("p1", 0)
        self.validation = {"valid": True, "errors": [], "warnings": [], "suggested_fixes": []}

    def device(self, item_id, **overrides):
        return {
            "proposed_id": item_id, "device_type": "socket", "space_id": self.space_id,
            "host_id": self.wall_id, "position": [4.9, -1.0, 3.35], "orientation": {"yaw": 90},
            "module_id": "socket-01", "dimensions_m": [0.08, 0.03, 0.09],
            "reason": "Gần bàn làm việc", "rule_ids": ["rule-1"],
            **overrides,
        }

    def save(self, devices, previous=None, *, approve=True, project="p1", result=None):
        revision = {}
        if previous is not None:
            revision = {"plan_id": previous["plan_id"], "expected_version": previous["version"]}
        draft = draft_save_placements(project, devices, self.validation if result is None else result, **revision)
        if approve:
            approve_placement_plan(project, draft["plan_id"], draft["version"], reviewer_id="engineer")
        return draft

    def apply(self, draft, project="p1"):
        return apply_approved_placement_plan(draft["plan_id"], draft["version"], project)

    def test_create_ifc_devices_type_host_pose_and_geometry(self):
        proposal = self.device("a")
        draft = self.save([proposal, self.device("b", position={"x": 4.9, "y": 0.0, "z": 3.35})])
        result = self.apply(draft)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["model_version"], 1)
        self.assertEqual(len(result["created_ids"]), 2)
        output = get_project_ifc_path("p1")
        model = open_model(output)
        a, b = (model.by_guid(guid) for guid in result["created_ids"])
        self.assertEqual(a.is_a(), "IfcOutlet")
        self.assertEqual(ifcopenshell.util.element.get_type(a), ifcopenshell.util.element.get_type(b))
        self.assertEqual(len(model.by_type("IfcOutletType")), 1)
        self.assertEqual(ifcopenshell.util.element.get_container(a).GlobalId, self.space_id)
        link = model.by_type("IfcRelConnectsElements")[0]
        self.assertEqual(link.RelatingElement.GlobalId, self.wall_id)
        self.assertEqual(link.RelatedElement, a)
        pose = ifcopenshell.util.placement.get_local_placement(a.ObjectPlacement)
        np.testing.assert_allclose(pose[:3, 3], [4900, -1000, 3350])
        np.testing.assert_allclose(pose[:3, 0], [0, 1, 0], atol=1e-8)
        shape = ifcopenshell.geom.create_shape(ifcopenshell.geom.settings(), a)
        points = np.asarray(shape.geometry.verts).reshape(-1, 3)
        np.testing.assert_allclose(np.ptp(points, axis=0), [0.08, 0.03, 0.09], atol=1e-8)
        pset = ifcopenshell.util.element.get_pset(a, "MVP_Placement")
        self.assertEqual(json.loads(pset["Proposal"]), proposal)
        self.assertEqual(pset["HostGlobalId"], self.wall_id)
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(str(output), logger)
        self.assertEqual(logger.statements, [])
        self.assertEqual(digest(self.source), self.source_digest)
        self.assertEqual(digest(self.baseline), self.source_digest)

    def test_update_delete_and_replay_preserve_ids_and_history(self):
        draft = self.save([self.device("a"), self.device("b"), self.device("keep")])
        first = self.apply(draft)
        a, b, keep = first["created_ids"]
        output1 = get_project_ifc_path("p1")
        digest1 = digest(output1)
        second = self.save([
            self.device("a", device_type="light", module_id="light-1", host_id=None),
            self.device("c"), self.device("keep"),
        ], draft)
        result = self.apply(second)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["updated_ids"], [a])
        self.assertEqual(result["removed_ids"], [b])
        self.assertEqual(len(result["created_ids"]), 1)
        self.assertEqual(result["model_version"], 2)
        model = open_model(get_project_ifc_path("p1"))
        self.assertEqual(model.by_guid(a).is_a(), "IfcLightFixture")
        self.assertEqual(model.by_guid(keep).is_a(), "IfcOutlet")
        with self.assertRaises(RuntimeError):
            model.by_guid(b)
        self.assertEqual(digest(output1), digest1)
        self.assertEqual(open_model(output1).by_guid(a).is_a(), "IfcOutlet")
        for old_draft in (second, draft):
            replay = self.apply(old_draft)
            self.assertEqual(replay, {
                "status": "already_applied", "model_version": 2,
                "created_ids": [], "updated_ids": [], "removed_ids": [], "errors": [],
            })

    def test_requires_exact_project_and_approved_version(self):
        draft = self.save([self.device("a")], approve=False)
        self.assertIn("approval", self.apply(draft)["errors"][0])
        self.assertIn("not found", self.apply(draft, project="p2")["errors"][0])
        with self.assertRaisesRegex(PlacementApplicationError, "passed validation"):
            self.save(
                [self.device("b")], result={**self.validation, "valid": False, "errors": ["blocked"]},
            )
        newer = self.save([], draft, approve=False)
        with self.assertRaisesRegex(PlacementApplicationError, "newer draft"):
            approve_placement_plan("p1", draft["plan_id"], 1, reviewer_id="engineer")
        self.assertEqual(self.apply(newer)["status"], "failed")
        self.assertEqual(get_project_ifc_path("p1"), self.baseline)

    def test_pending_newer_version_does_not_inherit_approval(self):
        draft = self.save([self.device("a")])
        second = self.save([self.device("b")], draft, approve=False)
        self.assertEqual(self.apply(second)["status"], "failed")
        self.assertEqual(self.apply(draft)["status"], "applied")

    def test_newer_approval_supersedes_unapplied_approval(self):
        draft = self.save([self.device("a")])
        second = self.save([self.device("b")], draft)
        self.assertIn("latest approved", self.apply(draft)["errors"][0])
        result = self.apply(second)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(len(result["created_ids"]), 1)
        self.assertEqual(self.apply(draft)["status"], "failed")

    def test_invalid_ifc_references_roll_back_partial_batch(self):
        draft = self.save([self.device("a"), self.device("b", host_id="missing-wall")])
        result = self.apply(draft)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("host_id", result["errors"][0])
        self.assertEqual(result["created_ids"], [])
        self.assertEqual(get_project_ifc_path("p1"), self.baseline)
        self.assertEqual(open_model(self.baseline).by_type("IfcOutlet"), [])
        self.assertEqual(self.apply(draft)["status"], "failed")
        self.assertEqual(len(list(self.baseline.parent.glob("*.ifc"))), 1)

    def test_readback_failure_then_retry(self):
        draft = self.save([self.device("a")])
        with patch("ifc_engine.writer.verify_batch", side_effect=RuntimeError("readback failed")):
            self.assertIn("readback failed", self.apply(draft)["errors"])
        self.assertEqual(get_project_ifc_path("p1"), self.baseline)
        self.assertEqual(len(list(self.baseline.parent.glob("*.ifc"))), 1)
        self.assertEqual(self.apply(draft)["status"], "applied")

    def test_receipt_failure_keeps_baseline_and_can_retry(self):
        draft = self.save([self.device("a")])
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("""
                CREATE TRIGGER fail_receipt AFTER INSERT ON placement_applications
                BEGIN SELECT RAISE(FAIL, 'receipt failed'); END
            """)
        self.assertIn("receipt failed", self.apply(draft)["errors"])
        self.assertEqual(get_project_ifc_path("p1"), self.baseline)
        with self.assertRaises(PlacementApplicationError):
            get_project_ifc_path("p1", 1)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("DROP TRIGGER fail_receipt")
        self.assertEqual(self.apply(draft)["model_version"], 1)

    def test_concurrent_replay_publishes_once(self):
        draft = self.save([self.device("a")])
        start = Barrier(2, timeout=10)

        def run(_):
            start.wait()
            return self.apply(draft)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, range(2)))
        self.assertCountEqual([item["status"] for item in results], ["applied", "already_applied"])
        self.assertTrue(all(item["model_version"] == 1 for item in results))
        self.assertEqual(len(open_model(get_project_ifc_path("p1")).by_type("IfcOutlet")), 1)

    def test_identical_snapshot_does_not_publish_new_model(self):
        draft = self.save([self.device("a"), self.device("b")])
        self.assertEqual(self.apply(draft)["status"], "applied")
        second = self.save([self.device("b"), self.device("a")], draft)
        self.assertEqual(self.apply(second), {
            "status": "applied", "model_version": 1,
            "created_ids": [], "updated_ids": [], "removed_ids": [], "errors": [],
        })
        self.assertEqual(self.apply(second)["status"], "already_applied")

    def test_removal_cannot_delete_objects_from_other_plans(self):
        a = self.save([self.device("a")])
        guid_a = self.apply(a)["created_ids"][0]
        b = self.save([self.device("a")])
        guid_b = self.apply(b)["created_ids"][0]
        self.assertNotEqual(guid_a, guid_b)
        result = self.apply(self.save([], a))
        self.assertEqual(result["removed_ids"], [guid_a])
        self.assertEqual(open_model(get_project_ifc_path("p1")).by_type("IfcOutlet")[0].GlobalId, guid_b)

    def test_missing_and_tampered_baseline_fail(self):
        draft = self.save([], project="p2")
        self.assertIn("baseline", self.apply(draft, project="p2")["errors"][0])
        self.baseline.write_bytes(b"tampered")
        result = self.apply(self.save([]))
        self.assertIn("integrity check", result["errors"][0])

    def test_removal_rejects_external_ifc_references(self):
        first = self.save([self.device("a")])
        first_result = self.apply(first)
        path = get_project_ifc_path("p1")
        model = open_model(path)
        device = model.by_guid(first_result["created_ids"][0])
        group = ifcopenshell.api.group.add_group(model, name="Existing circuit group")
        ifcopenshell.api.group.assign_group(model, products=[device], group=group)
        model.write(str(path))
        # Simulate an authorized later workflow updating the model metadata.
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("UPDATE project_ifc_versions SET sha256 = ? WHERE version = 1", (digest(path),))
        result = self.apply(self.save([], first))
        self.assertIn("IfcRelAssignsToGroup", result["errors"][0])
        self.assertEqual(get_project_ifc_path("p1"), path)

    def test_unsupported_ifc_schema_is_rejected(self):
        unsupported = self.source.with_name("ifc43.ifc")
        make_ifc_fixture(unsupported, schema="IFC4X3")
        with self.assertRaisesRegex(PlacementApplicationError, "Expected an IFC4 file"):
            register_project_ifc("p2", unsupported)
        with self.assertRaises(PlacementApplicationError):
            get_project_ifc_path("p2")
