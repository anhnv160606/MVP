"""Kiểm thử tích hợp Tool 3–6 với dữ liệu Supabase của dự án."""

import json
import unittest

import pytest

from tools.ifc_read import (
    _get_supabase_client,
    _normalize_text,
    _playbook_rows,
    playbook_get_load_class,
    playbook_get_object_rules,
    playbook_get_terminal_class,
    playbook_query_devices,
)


@pytest.mark.live
class TestPlaybookTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = _get_supabase_client()

    @classmethod
    def tearDownClass(cls):
        # Đóng kết nối HTTP/2 của postgrest client để hết ResourceWarning
        if hasattr(cls.client, "postgrest") and hasattr(cls.client.postgrest, "session"):
            cls.client.postgrest.session.close()

    @staticmethod
    def in_ket_qua(tool_name, result):
        print(f"\nKết quả {tool_name}:")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    def test_tool_3_prints_object_and_room_rules(self):
        result = playbook_get_object_rules(
            device_type="đèn trần",
            space_type="Phòng ngủ",
            project_standard="TCVN 9206:2012",
        )
        self.in_ket_qua("Tool 3", result)
        self.assertEqual(set(result), {"rules"})
        self.assertIsInstance(result["rules"], list)
        source_rules = _playbook_rows(self.client, "quy_tac_doi_tuong")
        if not any(row.get("ma_quy_tac") == "NHẬN-001" for row in source_rules):
            self.skipTest("Supabase chưa nạp dữ liệu NHẬN-001 cho quy_tac_doi_tuong.")
        self.assertIn("NHẬN-001", {rule.get("rule_id") for rule in result["rules"]})

    def test_tool_4_prints_catalog_records(self):
        result = playbook_query_devices(category="Áptômát tép", filters={})
        self.in_ket_qua("Tool 4", result)
        self.assertIsInstance(result, list)
        sources = (
            ("mo_dun_thiet_bi", "ten_cau_hinh"),
            ("thiet_bi_dong_cat", "ma_cau_hinh_mo_ta"),
            ("dau_noi_dien", "ten_dau_noi"),
        )
        has_aptomat = any(
            "aptomat" in _normalize_text(" ".join(str(value or "") for value in row.values()))
            for table, _ in sources
            for row in _playbook_rows(self.client, table)
        )
        if not has_aptomat:
            self.skipTest("Supabase chưa nạp mẫu aptomat vào các bảng catalog.")
        self.assertTrue(result, "Supabase chưa có mẫu aptomat trong catalog mô đun.")
        for device in result:
            self.assertEqual(
                set(device),
                {"module_id", "name", "ifc_class", "specs", "rated_power", "geometry_ref"},
            )

        den_result = playbook_query_devices(category="đèn trần", filters={})
        self.in_ket_qua("Tool 4 - đèn trần", den_result)
        self.assertIsInstance(den_result, list)

    def test_tool_5_prints_load_class(self):
        result = playbook_get_load_class(
            space_type="Phòng ngủ",
            device_category="Ổ cắm",
        )
        self.in_ket_qua("Tool 5", result)
        self.assertEqual(set(result), {"load_class_id", "load_class_name", "load_type"})
        source_loads = _playbook_rows(self.client, "lop_phu_tai")
        if not any(row.get("ma_lop_phu_tai") == "TẢI-003" for row in source_loads):
            self.skipTest("Supabase chưa nạp dữ liệu TẢI-003 cho lop_phu_tai.")
        self.assertEqual(result["load_class_id"], "TẢI-003")

    def test_tool_6_prints_terminal_class(self):
        result = playbook_get_terminal_class(
            device_type="đèn trần",
            connection_requirements={"phases": ["1 pha"]},
        )
        self.in_ket_qua("Tool 6", result)
        self.assertEqual(
            set(result), {"terminal_class_id", "voltage", "phases", "connection_specs"}
        )
        source_terminals = _playbook_rows(self.client, "lop_dau_cuoi")
        if not any(row.get("ma_lop_dau_cuoi") == "ĐIỂM-001" for row in source_terminals):
            self.skipTest("Supabase chưa nạp dữ liệu ĐIỂM-001 cho lop_dau_cuoi.")
        self.assertEqual(result["terminal_class_id"], "ĐIỂM-001")
        self.assertEqual(result["phases"], ["1 pha"])


if __name__ == "__main__":
    unittest.main()
