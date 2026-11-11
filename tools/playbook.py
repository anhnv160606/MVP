"""Read-only queries against the Supabase Playbook tables.

Pass an authenticated Supabase Python client to each function.  Keeping the
client injected avoids owning credentials or coupling the tool layer to a
particular Supabase SDK version.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable


TABLES = {
    "modules": "mo_dun_thiet_bi",
    "loads": "lop_phu_tai",
    "terminals": "lop_dau_cuoi",
    "object_rules": "quy_tac_doi_tuong",
    "room_rules": "quy_tac_phong",
    "design_rules": "quy_tac_thiet_ke",
    "standards": "nguon_tieu_chuan",
}


def _rows(client: Any, table: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    query = client.table(table).select("*")
    for key, value in (filters or {}).items():
        if value is not None:
            query = query.eq(key, value)
    result = query.execute()
    data = getattr(result, "data", None)
    if data is None and isinstance(result, dict):
        data = result.get("data")
    return [dict(row) for row in (data or [])]


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).replace("đ", "d").split())


def _contains(haystack: Any, needle: Any) -> bool:
    return _norm(needle) in _norm(haystack)


def playbook_query_devices(client: Any, category: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Find matching rows in the current module catalog without inventing specs."""
    filters = filters or {}
    rows = _rows(client, TABLES["modules"])
    tokens = [_norm(category), *(_norm(x) for x in (filters.get("keywords") or []))]
    rows = [r for r in rows if all(not t or _contains(r.get("nhom_thiet_bi", "") + " " + r.get("ten_cau_hinh", ""), t) for t in tokens)]
    if filters.get("rated_current_a") is not None:
        rows = [r for r in rows if r.get("dong_dinh_muc_goi_y_a") is not None and r["dong_dinh_muc_goi_y_a"] >= filters["rated_current_a"]]
    devices = [{
        "module_id": row.get("id"), "name": row.get("ten_cau_hinh"),
        "category": row.get("nhom_thiet_bi"), "ifc_class": None,
        "specs": {k: v for k, v in row.items() if k not in {"id", "ten_cau_hinh", "nhom_thiet_bi"}},
        "rated_power": None, "geometry_ref": None,
        "data_status": row.get("tinh_trang_kiem_chung"),
    } for row in rows]
    warnings = []
    if not devices:
        warnings.append("Không có mẫu khớp trong bảng mô-đun hiện tại; bảng này chủ yếu mô tả thiết bị đóng cắt, không phải catalog đèn/ổ cắm.")
    if devices and any(x["rated_power"] is None or x["ifc_class"] is None for x in devices):
        warnings.append("Playbook chưa có công suất định mức, lớp IFC hoặc hình học cho các mẫu trả về.")
    return {"devices": devices, "warnings": warnings}


def playbook_get_load_class(client: Any, space_type: str, device_category: str) -> dict[str, Any]:
    """Classify a load; demand/diversity factors are deliberately not inferred."""
    rows = _rows(client, TABLES["loads"])
    query = _norm(device_category)
    matches = [r for r in rows if query and (_contains(r.get("ten_lop_phu_tai"), query) or _contains(r.get("nhom_phu_tai"), query))]
    if not matches and query:
        matches = [r for r in rows if any(t in query for t in _norm(r.get("ten_lop_phu_tai")).split() if len(t) > 3)]
    load_classes = [{"load_class_id": r.get("ma_lop_phu_tai"), "load_class_name": r.get("ten_lop_phu_tai"),
                              "load_type": r.get("nhom_phu_tai"), "connected_power_range_W": [r.get("cong_suat_min_w"), r.get("cong_suat_max_w")],
                              "demand_factor": r.get("he_so_nhu_cau"), "diversity_factor": None,
                              "source": r.get("nguon_quy_tac"),
                              "data_status": r.get("tinh_trang_kiem_chung")} for r in matches]
    warnings = []
    if not matches:
        warnings.append("Không tìm thấy lớp phụ tải phù hợp; không tự gán hệ số nhu cầu hoặc đồng thời.")
    elif len(matches) > 1:
        warnings.append("Có nhiều lớp phụ tải phù hợp; cần chọn lớp theo thiết bị thực tế.")
    if matches and any(r.get("he_so_nhu_cau") is None for r in matches):
        warnings.append("Playbook chưa xác nhận hệ số nhu cầu cho một số lớp; diversity factor cũng không được suy ra.")
    return {"space_type": space_type, "device_category": device_category, "load_classes": load_classes, "warnings": warnings}


def playbook_get_terminal_class(client: Any, device_type: str, connection_requirements: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return compatible terminal classes from lop_dau_cuoi and linked loads."""
    requirements = connection_requirements or {}
    terminals = _rows(client, TABLES["terminals"])
    query = _norm(device_type)
    matches = [r for r in terminals if not query or _contains(r.get("ten_lop_dau_cuoi"), query)
               or _contains(r.get("kieu_diem_ket_noi"), query) or _contains(r.get("khong_gian_ap_dung"), query)]
    output = []
    for row in matches:
        phases = requirements.get("phases")
        supply = _norm(row.get("kieu_cap_dien"))
        if phases and phases == 3 and "ba pha" not in supply:
            continue
        if phases and phases == 1 and "mot pha" not in supply:
            continue
        output.append({"terminal_class_id": row.get("ma_lop_dau_cuoi"), "terminal_class_name": row.get("ten_lop_dau_cuoi"),
                       "voltage": requirements.get("voltage_v"), "phases": phases,
                       "connection_specs": {"supply_type": row.get("kieu_cap_dien"), "connection_type": row.get("kieu_diem_ket_noi"),
                                            "circuit_group": row.get("nhom_mach"), "power_factor": row.get("he_so_cong_suat_tam"),
                                            "load_class_id": row.get("lop_phu_tai_ma"), "space_scope": row.get("khong_gian_ap_dung"),
                                            "voltage_drop_limit_pct": row.get("gioi_han_sut_ap_pct"), "source_note": row.get("ghi_chu")},
                       "data_status": row.get("tinh_trang_kiem_chung")})
    warnings = []
    if not output:
        warnings.append("Không tìm thấy lớp đầu cuối phù hợp; điện áp/số pha cần được cung cấp từ hồ sơ thiết bị hoặc dự án.")
    if output and (requirements.get("voltage_v") is None or requirements.get("phases") is None):
        warnings.append("Điện áp hoặc số pha chưa có trong yêu cầu kết nối; trường này không được tự suy đoán.")
    return {"device_type": device_type, "terminal_classes": output, "warnings": warnings}


def playbook_get_object_rules(client: Any, device_type: str, space_type: str, project_standard: str | None = None) -> dict[str, Any]:
    """Combine IFC object mappings, room guidance and explicit design rules."""
    object_rules = _rows(client, TABLES["object_rules"])
    room_rules = _rows(client, TABLES["room_rules"])
    design_rules = _rows(client, TABLES["design_rules"])
    terminals = _rows(client, TABLES["terminals"])
    loads = _rows(client, TABLES["loads"])
    d, s = _norm(device_type), _norm(space_type)
    objects = [r for r in object_rules if (not d or any(_contains(x, d) or _contains(d, x) for x in r.get("tu_khoa_nhan_dien", [])))
               and (not s or not r.get("khong_gian_thuong_gap") or _contains(r.get("khong_gian_thuong_gap"), s))]
    rooms = [r for r in room_rules if not s or _contains(r.get("loai_khong_gian"), s)]
    applicable_design = [r for r in design_rules if not d or _contains(r.get("ten_quy_tac"), d) or _contains(r.get("phan_loai"), d)]
    standards = _rows(client, TABLES["standards"])
    sources = [r for r in standards if not project_standard or _contains(r.get("so_hieu_van_ban"), project_standard)]
    rules = []
    for row in objects:
        terminal = next((t for t in terminals if t.get("ma_lop_dau_cuoi") == row.get("lop_dau_cuoi_ma")), {})
        load = next((item for item in loads if item.get("ma_lop_phu_tai") == terminal.get("lop_phu_tai_ma")), {})
        rules.append({"rule_id": row.get("ma_quy_tac"), "quantity_rule": None, "spacing": None, "mounting_height": None,
                      "host_type": None, "constraints": [row.get("dieu_kien_bo_sung"), row.get("xu_ly_canh_bao")],
                      "source": load.get("nguon_quy_tac"), "source_status": row.get("tinh_trang_kiem_chung"),
                      "scope": row.get("khong_gian_thuong_gap"), "raw_rule": row})
    for row in rooms:
        rules.append({"rule_id": f"room:{row.get('loai_khong_gian')}", "quantity_rule": None, "spacing": None,
                      "mounting_height": row.get("cao_do_lap_dat"), "host_type": None,
                      "constraints": [row.get("nguyen_tac_bo_tri"), row.get("kiem_tra_bo_sung"), row.get("gioi_han_canh_bao")],
                      "source": None, "source_status": row.get("loai_du_lieu"), "scope": row.get("loai_khong_gian"), "raw_rule": row})
    for row in applicable_design:
        rules.append({"rule_id": row.get("ma_quy_tac"), "quantity_rule": None, "spacing": None, "mounting_height": None,
                      "host_type": None, "constraints": [row.get("dau_vao_can_thiet"), row.get("tieu_chi_xu_ly")],
                      "source": row.get("nguon_tham_khao"), "source_status": row.get("trang_thai_xac_nhan"),
                      "scope": row.get("phan_loai"), "raw_rule": row})
    warnings = []
    if not rules:
        warnings.append("Không tìm thấy rule thiết kế khớp thiết bị/phòng trong Playbook.")
    if rules and all(x["quantity_rule"] is None and x["spacing"] is None for x in rules):
        warnings.append("Playbook chưa quy định số lượng/khoảng cách định lượng cho quy tắc tìm được; cần đầu vào dự án hoặc kỹ sư xác nhận.")
    if any(not rule.get("source") for rule in rules):
        warnings.append("Một số rule trong Playbook không có nguồn truy vết trực tiếp ở schema hiện tại.")
    if project_standard and not sources:
        warnings.append(f"Không tìm thấy nguồn tiêu chuẩn khớp '{project_standard}'.")
    return {"device_type": device_type, "space_type": space_type, "project_standard": project_standard,
            "rules": rules, "sources": sources, "warnings": warnings}
