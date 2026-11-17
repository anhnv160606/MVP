"""Kiểm thử Tool 1 với không gian thật trong tệp IFC."""

import json
import unittest

import pytest

from ifc_engine.reader import IfcReader
from tools.ifc_read import _default_ifc_path, ifc_get_placement_context


@pytest.mark.ifc
class TestIfcGetPlacementContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reader = IfcReader(_default_ifc_path())
        spaces = reader.get_spaces()
        if not spaces:
            raise RuntimeError("Tệp IFC không có IfcSpace để kiểm thử Tool 1.")
        cls.space_id = spaces[0]["id"]

    def test_tool_returns_required_output_for_ifc_space(self):
        result = ifc_get_placement_context(space_id=self.space_id)
        print("\nKết quả Tool 1:")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        self.assertEqual(
            set(result),
            {
                "space_type",
                "geometry",
                "walls",
                "ceiling",
                "doors",
                "windows",
                "obstacles",
                "coordinate_system",
                "missing_data",
            },
        )
        self.assertIsInstance(result["geometry"], dict)
        for key in ("walls", "ceiling", "doors", "windows", "obstacles", "missing_data"):
            self.assertIsInstance(result[key], list)
        self.assertIsInstance(result["coordinate_system"], dict)


if __name__ == "__main__":
    unittest.main()
