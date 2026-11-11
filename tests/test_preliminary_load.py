import copy
import json
from pathlib import Path
import unittest

from database.project_store import read_snapshot, snapshot_from_dict
from ifc_engine.reader import SpatialIndex, read_spaces
from tools.calculation import calc_preliminary_load
from tools.playbook import LoadClass, Playbook, read_load_catalog


ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "database/Playbook_dien_nha_pho_Viet_Nam_2-5_tang.xlsx"
IFC = ROOT / "database/A.ifc"
INPUT = ROOT / "examples/preliminary-load.json"
BED = "12bXDOM85FnBBK679nyVuK"
FLOOR = "1SjtEBLuPDv9dv5HC$vbTn"
LIGHT, OUTLET, DESK = "TẢI-001", "TẢI-003", "TẢI-005"


class PreliminaryLoadTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(INPUT.read_text(encoding="utf-8"))
        self.model = SpatialIndex("model", {BED: FLOOR, "12bXDOM85FnBBK679nyVuO": FLOOR}, frozenset({FLOOR}))
        self.book = Playbook("book", {
            key: LoadClass(key, key, factor, scope, "fixture")
            for key, factor, scope in (
                (LIGHT, 1, "full_load_scenario"),
                (OUTLET, 0.8, "receptacle_circuit"),
                (DESK, 0.8, "receptacle_circuit"),
            )
        })

    def calculate(self, scope="room", key=BED, **kwargs):
        snapshot = snapshot_from_dict(self.data, self.model)
        return calc_preliminary_load(
            snapshot.plan_id, snapshot.plan_revision, scope, key,
            snapshot=snapshot, playbook=self.book, **kwargs,
        )

    def test_questionnaire_dedup_and_partial_units(self):
        self.data["placements"].append(copy.deepcopy(self.data["placements"][2]))
        result = self.calculate()
        self.assertEqual(result["connected_load_W"], 140)
        self.assertEqual(result["connected_load_VA"], 360)
        self.assertEqual(result["coverage"]["connected_load_W"], "partial")
        self.assertIsNone(result["estimated_demand_W"])
        self.assertEqual(result["pending_requirements"], [])

    def test_scope_and_receptacle_factor(self):
        self.assertEqual(self.calculate("floor", FLOOR)["connected_load_W"], 150)
        lighting = self.calculate("zone", "bedroom-lighting")
        self.assertEqual(lighting["estimated_demand_W"], 20)
        self.assertEqual(lighting["peak_scenario_W"], "evening")
        self.assertEqual(self.calculate("zone", "bedroom-receptacles")["estimated_demand_VA"], 288)
        self.data["placements"][4].pop("circuit_id")
        result = self.calculate("zone", "bedroom-receptacles")
        self.assertEqual(result["connected_load_VA"], 360)
        self.assertIsNone(result["estimated_demand_VA"])
        self.assertIn("MISSING_RECEPTACLE_CIRCUIT", {item["code"] for item in result["warnings"]})

    def test_source_and_pf(self):
        light = self.data["placements"][0]
        light.update(power_factor=0.8, pf_source="Demo datasheet")
        result = self.calculate("zone", "bedroom-lighting")
        self.assertEqual(result["connected_load_VA"], 12.5)
        self.assertEqual(result["coverage"]["connected_load_VA"], "partial")
        self.assertIsNone(self.calculate("zone", "bedroom-lighting", ruleset_id=None)["estimated_demand_W"])

    def test_allowance_replaced_by_known_equipment(self):
        self.data["placements"][2]["outlet_id"] = "bed-outlet-1"
        result = self.calculate()
        self.assertEqual(result["connected_load_W"], 140)
        self.assertEqual(result["connected_load_VA"], 180)
        self.assertIn("ALLOWANCE_REPLACED", {item["code"] for item in result["warnings"]})

    def test_pending_and_duplicate_requirement(self):
        self.data["requirements"][0]["quantity"] = 2
        self.data["placements"][2]["requirement_unit"] = 1
        result = self.calculate()
        self.assertEqual(result["pending_requirements"][0]["pending_units"], [2])
        duplicate = dict(self.data["placements"][2], load_id="desk-duplicate")
        self.data["placements"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate allocation"):
            self.calculate()

    def test_snapshot_revision_and_empty_zone(self):
        original = self.calculate()
        self.data["plan_revision"] = "2"
        self.data["placements"][0]["input_W"] = 12
        updated = self.calculate()
        self.assertEqual(original["connected_load_W"], 140)
        self.assertEqual(updated["connected_load_W"], 142)
        self.data["zones"]["empty"] = []
        self.assertEqual(self.calculate("zone", "empty")["estimated_demand_VA"], 0)
        snapshot = snapshot_from_dict(self.data, self.model)
        with self.assertRaisesRegex(ValueError, "revision"):
            calc_preliminary_load("demo-house", "1", "room", BED, snapshot=snapshot, playbook=self.book)

    def test_reject_wrong_playbook_revision(self):
        self.data["playbook_revision"] = "another-playbook"
        with self.assertRaisesRegex(ValueError, "playbook_revision"):
            self.calculate()


@unittest.skipUnless(BOOK.is_file() and IFC.is_file(), "Requires the local Playbook and A.ifc")
class RealFixturesTest(unittest.TestCase):
    def test_real_ifc_and_playbook(self):
        model = read_spaces(IFC)
        book = read_load_catalog(BOOK)
        self.assertEqual((len(model.rooms), len(model.floors)), (11, 4))
        self.assertEqual(model.rooms[BED], FLOOR)
        self.assertEqual(len(book.classes), 21)
        self.assertEqual(book.classes[OUTLET].factor, 0.8)
        self.assertIsNone(book.classes["TẢI-019"].factor)
        self.assertIsNone(book.classes["TẢI-021"].factor)
        snapshot = read_snapshot(INPUT, model)
        result = calc_preliminary_load("demo-house", "1", "zone", "bedroom-receptacles",
                                       snapshot=snapshot, playbook=book)
        self.assertEqual((result["connected_load_VA"], result["estimated_demand_VA"]), (360, 288))


if __name__ == "__main__":
    unittest.main()
