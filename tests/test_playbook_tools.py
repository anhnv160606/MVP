"""Kiểm thử tích hợp các tool Playbook với dữ liệu Supabase đã cấu hình."""

import json
import unittest

from tools.ifc_read import (
    playbook_get_load_class,
    playbook_get_object_rules,
    playbook_get_terminal_class,
    playbook_query_devices,
)


class TestPlaybookTools(unittest.TestCase):
    @staticmethod
    def print_result(tool_name, result):
        print(f"\nKết quả {tool_name}:")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    def test_tool_3_prints_rules_from_supabase(self):
        result = playbook_get_object_rules(
            device_type="đèn trần",
            space_type="Phòng ngủ",
            project_standard="TCVN 9206:2012",
        )

        self.print_result("Tool 3", result)
        self.assertEqual(set(result), {"rules"})
        self.assertIsInstance(result["rules"], list)
        rule_ids = {rule.get("rule_id") for rule in result["rules"]}
        self.assertIn(
            "NHẬN-001",
            rule_ids,
            "Thiếu seed quy_tac_doi_tuong (NHẬN-001) trong Supabase.",
        )

    def test_tool_4_prints_catalog_results_from_supabase(self):
        result = playbook_query_devices(category="Áptômát tép", filters={})

        self.print_result("Tool 4", result)
        self.assertIsInstance(result, list)
        self.assertTrue(
            result,
            "Catalog mo_dun_thiet_bi chưa có dữ liệu aptomat trong Supabase.",
        )
        for device in result:
            self.assertEqual(set(device), {
                "module_id", "name", "ifc_class", "specs", "rated_power", "geometry_ref"
            })

        lighting_result = playbook_query_devices(category="đèn trần", filters={})
        self.print_result("Tool 4 (đèn trần)", lighting_result)
        self.assertIsInstance(lighting_result, list)

    def test_tool_5_prints_load_class_from_supabase(self):
        result = playbook_get_load_class(
            space_type="Phòng ngủ",
            device_category="Ổ cắm",
        )

        self.print_result("Tool 5", result)
        self.assertEqual(set(result), {"load_class_id", "load_class_name", "load_type"})
        self.assertEqual(
            result["load_class_id"],
            "TẢI-003",
            "Thiếu seed lop_phu_tai (TẢI-003) trong Supabase.",
        )

    def test_tool_6_prints_terminal_class_from_supabase(self):
        result = playbook_get_terminal_class(
            device_type="đèn trần",
            connection_requirements={"phases": ["1 pha"]},
        )

        self.print_result("Tool 6", result)
        self.assertEqual(set(result), {
            "terminal_class_id", "voltage", "phases", "connection_specs"
        })
        self.assertEqual(
            result["terminal_class_id"],
            "ĐIỂM-001",
            "Thiếu seed lop_dau_cuoi (ĐIỂM-001) trong Supabase.",
        )
        self.assertIsInstance(result["phases"], list)
        self.assertIsInstance(result["connection_specs"], dict)


if __name__ == "__main__":
    unittest.main()
