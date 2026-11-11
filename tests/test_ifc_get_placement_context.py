"""Test the public Tool 1 contract against the project's A.ifc model.

Run from the MVP repository root with:
    python -m unittest discover -s tests -p "test_ifc_get_placement_context.py" -v

Set A_IFC_PATH to use a different IFC file.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

import ifcopenshell

from database.project_store import ProjectStore
from tools.phase2 import build_phase2_toolset


DEFAULT_IFC_PATH = Path(__file__).resolve().parents[2] / "A.ifc"
IFC_PATH = Path(os.environ.get("A_IFC_PATH", str(DEFAULT_IFC_PATH)))
OUTPUT_FIELDS = {
    "space_type", "geometry", "walls", "ceiling", "doors", "windows",
    "obstacles", "coordinate_system", "missing_data", "warnings",
}


class TestIfcGetPlacementContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not IFC_PATH.is_file():
            raise FileNotFoundError(
                f"Không tìm thấy IFC: {IFC_PATH}. Đặt biến môi trường A_IFC_PATH để chỉ file khác."
            )
        cls.model: Any = ifcopenshell.open(str(IFC_PATH))
        cls.spaces = cls.model.by_type("IfcSpace")
        if not cls.spaces:
            raise AssertionError(f"IFC không có IfcSpace: {IFC_PATH}")

    def test_tool_accepts_only_space_id_and_returns_placement_context(self) -> None:
        space_id = str(self.spaces[0].GlobalId)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectStore(Path(temp_dir) / "project_state.sqlite")
            toolset = build_phase2_toolset(
                project_id="tool-1-test-project",
                store=store,
                supabase_client=None,
                ifc_model_provider=lambda: self.model,
            )

            tool_definition = next(
                tool["function"] for tool in toolset["tools"]
                if tool["function"]["name"] == "ifc_get_placement_context"
            )
            parameters = tool_definition["parameters"]
            self.assertEqual(set(parameters["properties"]), {"space_id"})
            self.assertEqual(parameters["required"], ["space_id"])

            # Public tool call: the only argument supplied by the caller is space_id.
            result = toolset["execute"](
                "ifc_get_placement_context",
                {"space_id": space_id},
            )

        self.assertEqual(set(result), OUTPUT_FIELDS)
        self.assertEqual(result["space_type"], "Phòng ngủ")
        self.assertIsInstance(result["geometry"], dict)
        estimated_area = result["geometry"].get("area_bbox_estimate_m2")
        self.assertIsNotNone(estimated_area, "IFC room bbox should produce an explicitly labeled area estimate")
        self.assertGreater(estimated_area, 0.01, "IFC room geometry may have been scaled down twice")
        self.assertNotIn("coordinate_system", result["missing_data"])
        self.assertIsInstance(result["missing_data"], list)
        self.assertIsInstance(result["warnings"], list)

        summary = {
            "ifc_path": str(IFC_PATH),
            "space_id_input": space_id,
            "space_type": result["space_type"],
            "geometry": result["geometry"],
            "counts": {
                key: len(result[key] or [])
                for key in ("walls", "doors", "windows", "obstacles")
            },
            "coordinate_system": result["coordinate_system"],
            "missing_data": result["missing_data"],
            "warnings": result["warnings"],
        }
        # ASCII escapes keep the report printable in Windows consoles using
        # legacy code pages that cannot encode every Vietnamese character.
        print("\nTool 1 result:\n" + json.dumps(summary, ensure_ascii=True, indent=2, default=str))


if __name__ == "__main__":
    unittest.main(verbosity=2)
