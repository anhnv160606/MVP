"""Tính dòng mạch và đề xuất thiết bị bảo vệ, tủ phân phối từ Playbook."""

from decimal import Decimal
from math import ceil
from typing import Any, TypedDict

from tools.calc_preliminary_load import quantity, validate_power_values
from tools.ifc_read import playbook_query_breakers, playbook_query_panels

MODULE_PITCH_MM = 18


class ProtectedCircuit(TypedDict):
    circuit_id: str
    breaker_rated_current_a: float


def _required_positive(value: float, field: str) -> Decimal:
    number = quantity(value, field, positive=True)
    if number is None:
        raise ValueError(f"Thiếu {field}")
    return number


def panel_select_breaker(
    phase_count: int,
    voltage_v: float,
    pole_count: int,
    load_w: float | None = None,
    load_va: float | None = None,
    power_factor: float | None = None,
) -> dict[str, Any]:
    """Tính dòng thiết kế và đề xuất MCB nhánh nhỏ nhất đủ dòng và đúng số cực.

    Truyền tải VA hoặc tải W kèm hệ số công suất. Điện áp là điện áp hai đầu
    tải một pha hoặc điện áp dây của tải ba pha cân bằng. Trả căn cứ tính,
    mức dư dòng, phương án thay thế và những kiểm tra chưa thực hiện.
    """
    if type(phase_count) is not int or phase_count not in (1, 3):
        raise ValueError("phase_count phải là 1 hoặc 3")
    allowed_poles = (1, 2) if phase_count == 1 else (3, 4)
    if type(pole_count) is not int or pole_count not in allowed_poles:
        raise ValueError(f"pole_count phải thuộc {allowed_poles} cho mạch {phase_count} pha")

    voltage = _required_positive(voltage_v, "voltage_v")
    watts, apparent_power, pf = validate_power_values(
        {"input_W": load_w, "input_VA": load_va, "power_factor": power_factor}
    )
    if apparent_power is None and (watts is None or pf is None):
        missing_data = ["load_va"]
        missing_data.extend(
            field for field, value in (("load_w", watts), ("power_factor", pf)) if value is None
        )
        return {
            "status": "missing_data",
            "design_current_a": None,
            "selected_breaker": None,
            "alternatives": [],
            "current_margin_a": None,
            "basis": None,
            "missing_data": missing_data,
            "warnings": ["Cần tải VA hoặc tải W kèm hệ số công suất để tính dòng."],
        }

    # Với tải W, đưa PF vào mẫu số để giữ độ chính xác khi so với dòng định mức.
    voltage_term = voltage if phase_count == 1 else Decimal(3).sqrt() * voltage
    formula_voltage = "U" if phase_count == 1 else "√3 × U_LL"
    if apparent_power is not None:
        current = apparent_power / voltage_term
        formula = f"I = VA / ({formula_voltage})"
    else:
        current = watts / (voltage_term * pf)
        formula = f"I = W / ({formula_voltage} × PF)"
    design_current = float(current)
    basis = {
        "formula": formula,
        "phase_count": phase_count,
        "voltage_v": voltage_v,
        "pole_count": pole_count,
        "load_w": load_w,
        "load_va": load_va,
        "power_factor": power_factor,
    }
    candidates = [
        item
        for item in playbook_query_breakers("CB", pole_count, design_current)
        if Decimal(str(item["rated_current_a"])) >= current
        and (item["rated_voltage_v"] is None or item["rated_voltage_v"] >= voltage)
    ]
    if not candidates:
        return {
            "status": "no_match",
            "design_current_a": design_current,
            "selected_breaker": None,
            "alternatives": [],
            "current_margin_a": None,
            "basis": basis,
            "missing_data": [],
            "warnings": ["Catalog không có MCB phù hợp với các điều kiện đã biết."],
        }

    selected, *alternatives = candidates
    return {
        "status": "preliminary",
        "design_current_a": design_current,
        "selected_breaker": selected,
        "alternatives": alternatives,
        "current_margin_a": float(Decimal(str(selected["rated_current_a"])) - current),
        "basis": basis,
        "missing_data": [
            *selected["missing_data"],
            "cable_ampacity_a",
            "prospective_short_circuit_current_ka",
        ],
        "warnings": [
            "Đề xuất sơ bộ, chưa kiểm tra phối hợp với dây và dòng ngắn mạch tại vị trí lắp."
        ],
    }


def panel_select_rccb(
    circuits: list[ProtectedCircuit],
    group_design_current_a: float,
    pole_count: int,
    residual_current_ma: float | None = None,
) -> dict[str, Any]:
    """Đề xuất RCCB cho nhóm mạch dựa trên dòng thiết kế của cả nhóm (A).

    Truyền mã từng mạch, dòng định mức CB nhánh, số cực RCCB và dòng rò yêu cầu
    (mA) nếu đã chốt. Hàm kiểm tra thêm định mức từng CB nhánh và giữ danh sách
    mạch được bảo vệ. Kết quả còn cần kiểm tra phối hợp với bảo vệ phía trên.
    """
    if type(pole_count) is not int or pole_count not in (2, 4):
        raise ValueError("pole_count của RCCB phải là 2 hoặc 4")
    group_current = quantity(group_design_current_a, "group_design_current_a")
    if group_current is None:
        raise ValueError("Thiếu group_design_current_a")
    residual_current = quantity(residual_current_ma, "residual_current_ma", positive=True)
    if not circuits:
        raise ValueError("Cần ít nhất một mạch trong nhóm")

    circuit_ids = []
    branch_currents = []
    for circuit in circuits:
        circuit_id = circuit.get("circuit_id")
        if not isinstance(circuit_id, str) or not circuit_id.strip():
            raise ValueError("Mỗi mạch phải có circuit_id")
        circuit_ids.append(circuit_id)
        branch_currents.append(
            _required_positive(circuit.get("breaker_rated_current_a"), "breaker_rated_current_a")
        )
    if len(set(circuit_ids)) != len(circuit_ids):
        raise ValueError("circuit_id không được trùng trong nhóm RCCB")

    max_branch_current = max(branch_currents)
    # Dòng của nhóm đến từ bước tính tải. Dòng CB nhánh là ràng buộc riêng.
    minimum_rating = max(group_current, max_branch_current)
    basis = {
        "group_design_current_a": group_design_current_a,
        "max_branch_breaker_current_a": float(max_branch_current),
        "min_rated_current_a": float(minimum_rating),
        "pole_count": pole_count,
        "required_residual_current_ma": residual_current_ma,
    }
    candidates = playbook_query_breakers("RCCB", pole_count, float(minimum_rating))
    if residual_current is not None:
        verified = [
            item
            for item in candidates
            if item["residual_current_ma"] is not None
            and Decimal(str(item["residual_current_ma"])) == residual_current
        ]
        candidates = verified or [
            item for item in candidates if item["residual_current_ma"] is None
        ]

    selected = candidates[0] if candidates else None
    missing_data = ["upstream_protection_coordination"]
    if residual_current is None:
        missing_data.append("required_residual_current_ma")
    if selected:
        missing_data.extend(selected["missing_data"])
    warnings = ["Chưa kiểm tra phối hợp RCCB với thiết bị bảo vệ quá dòng phía trên."]
    if selected is None:
        warnings.append("Catalog không có RCCB phù hợp với các điều kiện đã biết.")
    elif selected["residual_current_ma"] is None:
        warnings.append("Catalog chưa có dòng rò tác động để xác nhận mẫu RCCB này.")

    return {
        "status": "preliminary" if selected else "no_match",
        "selected_rccb": selected,
        "protected_circuit_ids": circuit_ids if selected else [],
        "basis": basis,
        "missing_data": missing_data,
        "warnings": warnings,
    }


def panel_select_cabinet(
    total_module_width_mm: float | None,
    phase_count: int,
    main_breaker_rated_current_a: float,
    panel_role: str,
    mounting_type: str | None = None,
) -> dict[str, Any]:
    """Chọn tủ đúng vai trò, số pha, đủ số ô và đủ dòng theo CB tổng.

    Tổng bề rộng là phần chiếm chỗ của tất cả thiết bị trong tủ, mỗi thiết bị
    tính một lần. Truyền null nếu còn thiết bị thiếu bề rộng. Kết quả trả số ô
    cần, số ô dự phòng và dữ liệu vật lý cho bước đặt tủ nếu catalog có.
    """
    if type(phase_count) is not int or phase_count not in (1, 3):
        raise ValueError("phase_count phải là 1 hoặc 3")
    if panel_role not in ("main", "floor"):
        raise ValueError("panel_role phải là main hoặc floor")
    main_rating = _required_positive(main_breaker_rated_current_a, "main_breaker_rated_current_a")
    width = quantity(total_module_width_mm, "total_module_width_mm")
    required_modules = ceil(width / MODULE_PITCH_MM) if width is not None else None
    basis = {
        "total_module_width_mm": total_module_width_mm,
        "module_pitch_mm": MODULE_PITCH_MM,
        "phase_count": phase_count,
        "main_breaker_rated_current_a": main_breaker_rated_current_a,
        "panel_role": panel_role,
        "requested_mounting_type": mounting_type,
    }
    result = {
        "selected_cabinet": None,
        "required_modules": required_modules,
        "available_modules": None,
        "spare_modules": None,
        "spare_percent": None,
        "placement_data_complete": False,
        "basis": basis,
        "missing_data": [],
        "warnings": [],
    }
    if required_modules is None:
        return {
            **result,
            "status": "missing_data",
            "missing_data": ["total_module_width_mm"],
            "warnings": ["Cần tổng bề rộng đầy đủ trước khi chọn số ô của tủ."],
        }

    candidates = playbook_query_panels(
        phase_count=phase_count,
        min_rated_current_a=float(main_rating),
        min_module_count=required_modules,
        mounting_type=mounting_type,
        panel_role=panel_role,
    )
    if not candidates:
        return {
            **result,
            "status": "no_match",
            "warnings": ["Catalog không có tủ đáp ứng các điều kiện đã cho."],
        }

    selected = candidates[0]
    available_modules = selected["module_count"]
    spare_modules = available_modules - required_modules
    placement_ready = selected["placement_data_complete"]
    return {
        **result,
        "status": "ok" if placement_ready else "preliminary",
        "selected_cabinet": selected,
        "available_modules": available_modules,
        "spare_modules": spare_modules,
        "spare_percent": 100 * spare_modules / available_modules,
        "placement_data_complete": placement_ready,
        "missing_data": selected["missing_data"],
        "warnings": [] if placement_ready else ["Thiếu thông số vật lý để kiểm tra vị trí lắp tủ."],
    }
