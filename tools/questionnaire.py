"""Questionnaire context used by Phase 2 load and placement tools."""
from __future__ import annotations

from typing import Any


def questionnaire_get_load_context(project_id: str | None = None, space_id: str | None = None,
                                  answers: dict[str, Any] | None = None, store: Any = None) -> dict[str, Any]:
    """Return the Phase 1 derived load context, optionally scoped to one IFC space.

    Accepted stored shape is either the normalized context itself, or a Phase 1
    submission containing ``derived_load_context``. Raw question/option IDs are
    deliberately not interpreted here because their meaning belongs to the
    Phase 1 questionnaire definition.
    """
    data = answers
    if data is None and store is not None and project_id:
        data = store.get_questionnaire(project_id)
    if not isinstance(data, dict) or not data:
        return {"per_space_requirements": {}, "declared_loads": [], "special_devices": [],
                "missing_data": ["questionnaire_answers"], "warnings": ["Chưa có câu trả lời bảng hỏi; không suy đoán nhu cầu điện."]}

    derived = data.get("derived_load_context")
    if isinstance(derived, dict):
        data = derived
    recognized = any(key in data for key in ("per_space_requirements", "declared_loads", "special_devices"))
    if not recognized:
        return {"per_space_requirements": {}, "declared_loads": [], "special_devices": [],
                "missing_data": ["derived_load_context"],
                "warnings": ["Có dữ liệu bảng hỏi nhưng chưa có derived_load_context theo schema Phase 2; cần Phase 1 ánh xạ câu trả lời, tool không tự diễn giải mã câu hỏi."]}

    warnings: list[str] = []
    raw_per_space = data.get("per_space_requirements", {})
    if not isinstance(raw_per_space, dict):
        warnings.append("per_space_requirements không phải object nên không thể lọc theo phòng.")
        raw_per_space = {}
    per_space = {str(key): value for key, value in raw_per_space.items()}
    if space_id is not None:
        per_space = {str(space_id): per_space.get(str(space_id), {})} if str(space_id) in per_space else {}
    raw_declared = data.get("declared_loads", [])
    if not isinstance(raw_declared, list):
        warnings.append("declared_loads không phải danh sách; trường này được trả rỗng.")
        raw_declared = []
    declared = [item for item in raw_declared if isinstance(item, dict)]
    if len(declared) != len(raw_declared):
        warnings.append("Một số phần tử declared_loads sai định dạng và đã bị bỏ qua.")
    if space_id is not None:
        declared = [item for item in declared if str(item.get("space_id")) == str(space_id)]
    raw_special = data.get("special_devices", [])
    if not isinstance(raw_special, list):
        warnings.append("special_devices không phải danh sách; trường này được trả rỗng.")
        raw_special = []
    special = [item for item in raw_special if isinstance(item, dict)]
    if len(special) != len(raw_special):
        warnings.append("Một số phần tử special_devices sai định dạng và đã bị bỏ qua.")
    if space_id is not None:
        special = [item for item in special if str(item.get("space_id")) == str(space_id)]
    missing = []
    has_space_requirements = str(space_id) in per_space and bool(per_space[str(space_id)]) if space_id is not None else bool(per_space)
    has_space_loads = bool(declared or special)
    if space_id is not None and not has_space_requirements and not has_space_loads:
        missing.append(f"requirements_for_space:{space_id}")
    if space_id is None and not per_space and not declared and not special:
        missing.append("load_requirements")
        warnings.append("Bảng hỏi đã có nhưng chưa khai báo yêu cầu phòng, phụ tải hoặc thiết bị đặc biệt.")
    return {"per_space_requirements": per_space, "declared_loads": declared, "special_devices": special,
            "missing_data": missing, "warnings": warnings + (["Không có khai báo nhu cầu cho phòng này."] if f"requirements_for_space:{space_id}" in missing else [])}


def questionnaire_submit(project_id: str, answers: dict[str, Any], store: Any) -> dict[str, Any]:
    if not project_id or not isinstance(answers, dict):
        raise ValueError("Cần project_id và answers dạng object.")
    store.save_questionnaire(project_id, answers)
    return {"project_id": project_id, "status": "saved",
            "derived_load_context": questionnaire_get_load_context(project_id, answers=answers)}
