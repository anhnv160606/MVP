import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from database.project_store import PlacementPlanNotFound, PlacementVersionConflict, ProjectStore
from tools.draft_save_placements import draft_save_placements


def proposal(proposed_id, position=None):
    return {
        "proposed_id": proposed_id,
        "device_type": "socket",
        "space_id": "room-1",
        "host_id": "wall-1",
        "position": [1, 2, 0.4] if position is None else position,
        "orientation": {"yaw": 90},
        "rule_ids": ["R1"],
        "reason": "Ổ cắm gần bàn làm việc",
    }


def validation(**overrides):
    return {"valid": True, "errors": [], "warnings": [], "suggested_fixes": [], **overrides}


class DraftSavePlacementsTest(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.db_path = Path(temp) / "data" / "project #1.db"
        self.enterContext(patch.dict(os.environ, {"MVP_PROJECT_DB_PATH": str(self.db_path)}))
        self.store = ProjectStore()

    def test_first_version_persists_failed_validation_and_reasoning(self):
        devices = [proposal("b"), proposal("a", {"x": -2, "y": 0, "z": 1.2})]
        result = validation(
            valid=False, errors=[{"code": "blocked", "proposed_id": "b"}],
            warnings=["Thiếu dữ liệu nội thất"], suggested_fixes=["Di chuyển ổ cắm"],
        )
        saved = draft_save_placements("p1", devices, result)
        self.assertEqual(saved["version"], 1)
        self.assertEqual(saved["status"], "pending_review")
        self.assertEqual(saved["diff_summary"], {
            "added": ["a", "b"], "updated": [], "removed": [], "unchanged": [],
        })

        reopened = ProjectStore(self.db_path).get_placement_plan("p1", saved["plan_id"], 1)
        self.assertEqual(reopened["proposals"], devices)
        self.assertEqual(reopened["validation_result"], result)
        self.assertEqual(reopened["diff_summary"], saved["diff_summary"])
        self.assertEqual(reopened["status"], saved["status"])

    def test_revision_diff_and_immutable_history(self):
        first_devices = [proposal("remove"), proposal("edit"), proposal("keep")]
        first = draft_save_placements("p1", first_devices, validation())
        plan_id = first["plan_id"]
        reordered_keys = dict(reversed(list(proposal("keep").items())))
        second_devices = [reordered_keys, proposal("add"), proposal("edit", [1, 2, 0.9])]
        second = draft_save_placements(
            "p1", second_devices, validation(warnings=["review"]), plan_id=plan_id, expected_version=1,
        )
        self.assertEqual(second["plan_id"], plan_id)
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["diff_summary"], {
            "added": ["add"], "updated": ["edit"], "removed": ["remove"], "unchanged": ["keep"],
        })
        self.assertEqual(self.store.get_placement_plan("p1", plan_id, 1)["proposals"], first_devices)
        self.assertEqual(self.store.get_placement_plan("p1", plan_id)["proposals"], second_devices)
        self.assertIsNone(self.store.get_placement_plan("p1", plan_id, 3))

    def test_empty_snapshot_removes_all_devices(self):
        first = draft_save_placements("p1", [proposal("a")], validation())
        second = draft_save_placements(
            "p1", [], validation(), plan_id=first["plan_id"], expected_version=1,
        )
        self.assertEqual(second["diff_summary"]["removed"], ["a"])
        self.assertEqual(self.store.get_placement_plan("p1", first["plan_id"])["proposals"], [])

    def test_metadata_change_updates_diff_and_new_validation_is_retained(self):
        devices = [proposal("a"), proposal("b")]
        first = draft_save_placements("p1", devices, validation())
        devices[0]["reason"] = "Thay đổi lý do bố trí"
        result = validation(warnings=["review"])
        second = draft_save_placements(
            "p1", list(reversed(devices)), result, plan_id=first["plan_id"], expected_version=1,
        )
        self.assertEqual(second["diff_summary"]["updated"], ["a"])
        self.assertEqual(second["diff_summary"]["unchanged"], ["b"])
        self.assertEqual(self.store.get_placement_plan("p1", first["plan_id"])["validation_result"], result)

    def test_uses_existing_project_database_and_only_changes_draft_tables(self):
        self.db_path.parent.mkdir()
        model = self.db_path.parent / "building.ifc"
        model.write_bytes(b"example IFC contents")
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("CREATE TABLE project_state (project_id TEXT)")
            connection.execute("INSERT INTO project_state VALUES ('p1')")
        self.assertIsNone(self.store.get_placement_plan("p1", "missing"))
        draft_save_placements("p1", [proposal("a")], validation())
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM project_state").fetchall(), [("p1",)])
        self.assertEqual(model.read_bytes(), b"example IFC contents")

    def test_projects_and_independent_plans_are_isolated(self):
        self.assertIsNone(self.store.get_placement_plan("p1", "missing"))
        self.assertFalse(self.db_path.exists())
        first = draft_save_placements("p1", [], validation())
        other = draft_save_placements("p1", [], validation())
        self.assertNotEqual(first["plan_id"], other["plan_id"])
        self.assertIsNone(self.store.get_placement_plan("p2", first["plan_id"]))
        with self.assertRaises(PlacementPlanNotFound):
            draft_save_placements("p2", [], validation(), plan_id=first["plan_id"], expected_version=1)

    def test_rejects_invalid_inputs_before_creating_database(self):
        cases = [
            ("", [], validation(), {}),
            ("p1", {}, validation(), {}),
            ("p1", [None], validation(), {}),
            ("p1", [proposal("a"), proposal("a")], validation(), {}),
            ("p1", [{**proposal("a"), "space_id": None}], validation(), {}),
            ("p1", [proposal("a", [0, 1])], validation(), {}),
            ("p1", [proposal("a", {"x": 1, "z": 0})], validation(), {}),
            ("p1", [proposal("a", [False, 1, 2])], validation(), {}),
            ("p1", [proposal("a", [float("inf"), 1, 2])], validation(), {}),
            ("p1", [proposal("a", [float("nan"), 1, 2])], validation(), {}),
            ("p1", [{**proposal("a"), "rule_ids": "R1"}], validation(), {}),
            ("p1", [{**proposal("a"), "reason": object()}], validation(), {}),
            ("p1", [], validation(valid=1), {}),
            ("p1", [], validation(errors=["blocked"]), {}),
            ("p1", [], validation(warnings=None), {}),
            ("p1", [], validation(suggested_fixes=[float("nan")]), {}),
            ("p1", [], validation(), {"expected_version": 1}),
            ("p1", [], validation(), {"plan_id": "unknown"}),
            ("p1", [], validation(), {"plan_id": "unknown", "expected_version": True}),
            ("p1", [], validation(), {"plan_id": "unknown", "expected_version": 0}),
        ]
        for index, (project, devices, result, options) in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(ValueError):
                    draft_save_placements(project, devices, result, **options)
        self.assertFalse(self.db_path.exists())

    def test_concurrent_revisions_reject_stale_update(self):
        first = draft_save_placements("p1", [], validation())
        plan_id = first["plan_id"]
        barrier = Barrier(2, timeout=5)

        def save(proposed_id):
            barrier.wait()
            try:
                return draft_save_placements(
                    "p1", [proposal(proposed_id)], validation(), plan_id=plan_id, expected_version=1,
                )
            except PlacementVersionConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, ("a", "b")))
        self.assertEqual(sum(result is None for result in results), 1)
        winner = next(result for result in results if result is not None)
        self.assertEqual(winner["version"], 2)
        self.assertEqual(self.store.get_placement_plan("p1", plan_id)["diff_summary"], winner["diff_summary"])
        self.assertIsNone(self.store.get_placement_plan("p1", plan_id, 3))

    def test_database_failure_rolls_back_and_allows_retry(self):
        first = draft_save_placements("p1", [proposal("a")], validation())
        plan_id = first["plan_id"]
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("""
                CREATE TRIGGER fail_revision AFTER INSERT ON placement_plan_versions
                WHEN NEW.version = 2
                BEGIN
                    SELECT RAISE(FAIL, 'simulated write failure');
                END
            """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "simulated write failure"):
            draft_save_placements("p1", [], validation(), plan_id=plan_id, expected_version=1)
        self.assertEqual(self.store.get_placement_plan("p1", plan_id)["version"], 1)

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("DROP TRIGGER fail_revision")
        retried = draft_save_placements("p1", [], validation(), plan_id=plan_id, expected_version=1)
        self.assertEqual(retried["version"], 2)


if __name__ == "__main__":
    unittest.main()
