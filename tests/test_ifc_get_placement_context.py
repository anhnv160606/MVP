"""Chạy Tool 1 với tệp IFC dùng chung và in đúng cấu trúc kết quả yêu cầu."""

import json
import unittest

from ifc_engine.reader import IfcReader
from tools.ifc_read import _default_ifc_path, ifc_get_placement_context


class TestIfcGetPlacementContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reader = IfcReader(_default_ifc_path())
        spaces = reader.get_spaces()
        if not spaces:
            raise RuntimeError("File IFC không có IfcSpace để kiểm tra Tool 1.")
        cls.space_id = spaces[0]["id"]

    def test_tool_returns_requested_output_for_real_ifc_space(self):
        result = ifc_get_placement_context(space_id=self.space_id)
        print("\nKết quả Tool 1:")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        self.assertEqual(set(result), {
            "space_type",
            "geometry",
            "walls",
            "ceiling",
            "doors",
            "windows",
            "obstacles",
            "coordinate_system",
            "missing_data",
        })
        self.assertIsInstance(result["geometry"], dict)
        self.assertIsInstance(result["walls"], list)
        self.assertIsInstance(result["ceiling"], list)
        self.assertIsInstance(result["doors"], list)
        self.assertIsInstance(result["windows"], list)
        self.assertIsInstance(result["obstacles"], list)
        self.assertIsInstance(result["coordinate_system"], dict)
        self.assertIsInstance(result["missing_data"], list)


if __name__ == "__main__":
    unittest.main()
