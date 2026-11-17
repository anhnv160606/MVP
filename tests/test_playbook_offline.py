from conftest import FakeClient

from tools.ifc_read import (
    _playbook_rows,
    playbook_client_context,
    playbook_get_load_class,
    playbook_get_object_rules,
    playbook_get_terminal_class,
    playbook_query_devices,
)


def test_catalog_pagination_and_ambiguity():
    client = FakeClient()
    client.rows["large"] = [{"id": n} for n in range(1001)]
    assert len(_playbook_rows(client, "large")) == 1001
    client.rows["lop_phu_tai"] = [
        {
            "id": 1,
            "ma_lop_phu_tai": "L1",
            "ten_lop_phu_tai": "Chiếu sáng",
            "nhom_phu_tai": "Lighting",
        }
    ]
    client.rows["lop_dau_cuoi"] = [
        {
            "id": 1,
            "ma_lop_dau_cuoi": "T1",
            "ten_lop_dau_cuoi": "Đèn trần",
            "lop_phu_tai_ma": "L1",
            "kieu_cap_dien": "Một pha hai dây",
            "dien_ap_v": 230,
        }
    ]
    client.rows["mo_dun_thiet_bi"] = [
        {"id": 1, "ten_cau_hinh": "Đèn trần", "ifc_class": "IfcLightFixture"}
    ]
    with playbook_client_context(client):
        assert playbook_query_devices("đèn trần")[0]["ifc_class"] == "IfcLightFixture"
        assert playbook_get_load_class("Bedroom", "lighting")["load_class_id"] == "L1"
        assert (
            playbook_get_terminal_class("đèn trần", {"voltage": 230})["terminal_class_id"] == "T1"
        )
        assert (
            playbook_get_terminal_class("đèn trần", {"voltage": 400})["terminal_class_id"] is None
        )
        assert playbook_get_object_rules("đèn trần", "Phòng ngủ", "project standard") == {
            "rules": []
        }
        client.rows["lop_phu_tai"].append(
            {"id": 2, "ma_lop_phu_tai": "L2", "ten_lop_phu_tai": "Chiếu sáng"}
        )
        assert playbook_get_load_class("Bedroom", "lighting")["load_class_id"] is None
