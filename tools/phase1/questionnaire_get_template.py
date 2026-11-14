"""Tool #1: questionnaire_get_template.

Tạo mẫu bảng hỏi, cấu hình câu hỏi và thiết bị (profiles & catalog),
nhóm phụ tải, và các định nghĩa kiểu dữ liệu cho Phase 1.
"""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
from typing import Any, Literal
import unicodedata
from uuid import uuid4

from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict, Field, model_validator

from database.project_store import require_id
from ifc_engine.reader import SpatialIndex

BUILDING_TYPE = "townhouse"
TEMPLATE_ID = "townhouse-electrical"
TEMPLATE_VERSION = "1"
CHECKLIST_TEMPLATE_ID = "townhouse-electrical-checklist"
CHECKLIST_TEMPLATE_VERSION = "1"

SELECTIONS = {
    "needed": "Có nhu cầu",
    "not_needed": "Không cần",
    "unknown": "Chưa rõ",
}

QUESTIONS = {
    "lighting": ("Phòng có cần chiếu sáng không?", "equipment"),
    "general_receptacle": ("Phòng cần thêm ổ cắm hoặc điểm cấp điện ở đâu?", "supply_point"),
    "desk_equipment": ("Phòng có thiết bị dùng điện tại bàn làm việc không?", "equipment"),
    "plug_in_equipment": ("Phòng có thiết bị cắm điện nào đã biết?", "equipment"),
    "air_conditioning": ("Phòng có cần điều hòa không?", "equipment"),
    "ventilation": ("Phòng có cần quạt thông gió không?", "equipment"),
    "water_heater": ("Phòng có cần bình nước nóng không?", "equipment"),
    "pump": ("Công trình có bơm cần cấp điện không?", "equipment"),
    "special_equipment": ("Có thiết bị riêng nào cần cấp điện không?", "equipment"),
}

POWER_FIELDS = ("input_W", "input_VA", "power_source", "power_factor", "pf_source")
ANSWER_FIELDS = {
    "answer_item_id", "q_id", "space_id", "selected", "quantity", "placement_hint",
    *POWER_FIELDS, "scope_type", "room_type",
}

ROOM_DEVICE_CLASSES = {"air_conditioners": "TẢI-006", "water_heater": "TẢI-007"}

PROFILES: dict[str, dict[str, Any]] = {
    "other": {
        "label": "Khác / chưa xác định",
        "keywords": [],
        "questions": [
            "lighting",
            "general_receptacle",
            "desk_equipment",
            "plug_in_equipment",
            "air_conditioning",
            "ventilation",
            "water_heater",
            "special_equipment",
        ],
    },
    "bedroom": {
        "label": "Phòng ngủ",
        "keywords": ["phong ngu", "p ngu"],
        "questions": [
            "lighting",
            "general_receptacle",
            "air_conditioning",
            "desk_equipment",
            "plug_in_equipment",
            "special_equipment",
        ],
    },
    "bathroom": {
        "label": "Phòng tắm / WC",
        "keywords": ["wc", "phong tam", "ve sinh"],
        "questions": [
            "lighting",
            "water_heater",
            "ventilation",
            "general_receptacle",
            "special_equipment",
        ],
    },
    "kitchen": {
        "label": "Bếp / phòng ăn",
        "keywords": ["bep", "phong an"],
        "questions": [
            "lighting",
            "general_receptacle",
            "plug_in_equipment",
            "ventilation",
            "special_equipment",
        ],
    },
    "living": {
        "label": "Phòng khách",
        "keywords": ["phong khach"],
        "questions": [
            "lighting",
            "general_receptacle",
            "air_conditioning",
            "plug_in_equipment",
            "special_equipment",
        ],
    },
    "outdoor": {
        "label": "Ban công / sân",
        "keywords": ["ban cong", "san"],
        "questions": ["lighting", "general_receptacle", "special_equipment"],
    },
    "building": {
        "label": "Toàn công trình",
        "keywords": [],
        "questions": ["pump", "special_equipment"],
    },
}

RECOMMENDATIONS: dict[str, list[str]] = {
    "building": ["TẢI-012", "TẢI-015", "TẢI-016", "TẢI-019", "TẢI-020", "TẢI-021"],
    "bedroom": ["TẢI-001", "TẢI-003", "TẢI-005", "TẢI-006", "TẢI-018"],
    "bathroom": ["TẢI-001", "TẢI-007", "TẢI-017"],
    "kitchen": ["TẢI-001", "TẢI-004", "TẢI-006", "TẢI-008", "TẢI-009", "TẢI-013", "TẢI-014"],
    "living": ["TẢI-001", "TẢI-002", "TẢI-005", "TẢI-006", "TẢI-018"],
    "outdoor": ["TẢI-001", "TẢI-012"],
    "other": [],
}

RECOMMENDATIONS_JSON_BYTES = json.dumps(RECOMMENDATIONS, indent=2, ensure_ascii=False).encode("utf-8")
RECOMMENDATIONS_REVISION = sha256(RECOMMENDATIONS_JSON_BYTES).hexdigest()


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFD", name.lower().replace("đ", "d"))
    return " ".join(re_sub_alphanumeric("".join(c for c in text if not unicodedata.combining(c))).split())


def re_sub_alphanumeric(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]", " ", text)


def suggest_profile(name: str) -> str:
    text = " " + normalize(name) + " "
    return next((key for key, profile in PROFILES.items()
                 if any(" " + word + " " in text for word in profile["keywords"])), "other")


def question_ids(profile: str) -> list[str]:
    return PROFILES[profile]["questions"]


def group_rooms(rooms: dict[str, str], overrides: dict[str, str] | None = None) -> dict[str, list[tuple[str, str]]]:
    """Nhóm phòng IFC theo chức năng để chọn tên phòng và tầng."""
    overrides = overrides or {}
    groups: dict[str, list[tuple[str, str]]] = {}
    order = [key for key in PROFILES if key not in {"building", "other"}] + ["other"]
    for key in order:
        members = [(space_id, name) for space_id, name in rooms.items()
                   if overrides.get(space_id, suggest_profile(name)) == key]
        if members:
            groups[key] = members
    return groups


@dataclass(frozen=True)
class ChecklistCatalog:
    revision: str
    items: dict[str, Any]
    recommendations: dict[str, list[str]]
    template_revision: str


def read_checklist_catalog(path: str | Path) -> ChecklistCatalog:
    data = Path(path).read_bytes()
    revision = sha256(data).hexdigest()
    mapping = RECOMMENDATIONS
    workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        sheet = workbook["Lớp phụ tải"]
        rows = sheet.iter_rows(min_row=4, max_col=11, values_only=True)
        header = next(rows)
        if header[:5] != ("Mã lớp phụ tải", "Tên lớp phụ tải", "Nhóm phụ tải",
                          "Công suất tham khảo nhỏ nhất (W)", "Công suất tham khảo lớn nhất (W)"):
            raise ValueError("Unexpected Playbook load headers")
        items = {}
        for number, row in enumerate(rows, 5):
            key, name, group, low, high, basis, factor, unit, notes, source, status = row
            if key is None:
                continue
            require_id(key, "load_class_id")
            if key in items:
                raise ValueError("Duplicate load_class_id")
            for value in (low, high, factor):
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError(f"Invalid catalog value: {key}")
            if low is not None and high is not None and low > high:
                raise ValueError(f"Inverted power range: {key}")
            if factor is not None and factor > 1:
                raise ValueError(f"Invalid demand factor: {key}")
            intent = "supply_point" if key in {"TẢI-002", "TẢI-003", "TẢI-004", "TẢI-005"} else "generation" if key == "TẢI-021" else "equipment"
            items[key] = {
                "load_class_id": key, "label": name, "group": group, "intent": intent,
                "reference_power_W": {"min": low, "max": high},
                "input_W": low if intent == "equipment" and low is not None and low == high else None,
                "input_VA": None, "power_factor": None,
                "demand_factor": factor, "basis": basis, "unit": unit,
                "notes": notes, "rule_source": source, "verification_status": status,
                "source": {"kind": "playbook", "revision": revision, "sheet": sheet.title,
                           "row": number, "power_cells": f"D{number}:E{number}", "factor_cell": f"G{number}"},
            }
        if not items:
            raise ValueError("Empty Playbook load catalog")
        if any(key not in items for keys in mapping.values() for key in keys):
            raise ValueError("Questionnaire mapping references missing Playbook entries")
        return ChecklistCatalog(revision, items, mapping, RECOMMENDATIONS_REVISION)
    finally:
        workbook.close()


def load_groups(catalog: ChecklistCatalog) -> list[dict[str, Any]]:
    """Tổng hợp khoảng công suất từ các lớp tải gốc trong Playbook."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for key, item in sorted(catalog.items.items()):
        if item["source"]["kind"] != "playbook":
            continue
        name = item["group"]
        groups.setdefault(name, []).append(item)
    result = []
    for name, members in sorted(groups.items()):
        sources = [{"load_class_id": item["load_class_id"], "reference_power_W": item["reference_power_W"],
                    "intent": item["intent"], "source": item["source"]} for item in members]
        low = [item["reference_power_W"]["min"] for item in members if item["reference_power_W"]["min"] is not None]
        high = [item["reference_power_W"]["max"] for item in members if item["reference_power_W"]["max"] is not None]
        intents = {item["intent"] for item in members}
        result.append({
            "group_name": name,
            "group_revision": sha256(json.dumps(sources, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "reference_power_W": {"min": min(low) if low else None, "max": max(high) if high else None},
            "intent": next(iter(intents)) if len(intents) == 1 else None,
            "range_basis": "min_of_minima_max_of_maxima",
            "reference_only": True,
            "range_complete": len(low) == len(members) and len(high) == len(members),
            "source_load_classes": sources,
        })
    return result


def room_device_options(catalog: ChecklistCatalog, room_type: str) -> list[str]:
    recommended = catalog.recommendations.get(room_type, [])
    return [field for field, load_id in ROOM_DEVICE_CLASSES.items() if load_id in recommended]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RoomChoice(StrictModel):
    space_id: str
    air_conditioners: int = Field(default=0, ge=0, le=2)
    water_heater: bool = False
    room_type: str | None = None


class KitchenChoice(StrictModel):
    space_id: str
    stove: Literal["gas", "electric"] = "gas"
    burners: Literal["single", "double"] | None = None
    oven: bool = False
    fridge: bool = False
    dishwasher_catalog_id: str | None = None
    microwave_catalog_id: str | None = None


class DeviceChoice(StrictModel):
    load_class_id: str
    space_id: str | None = None
    quantity: int = Field(default=1, ge=1)


class DesignInput(StrictModel):
    rooms: list[RoomChoice]
    shared_water_heaters_by_storey: bool = False
    kitchens: list[KitchenChoice] = Field(default_factory=list)
    pump: bool = False
    washing_machine_space_id: str | None = None
    custom_devices: list[DeviceChoice] = Field(default_factory=list)


class FormSubmission(StrictModel):
    template_id: str
    template_version: str
    template_revision: str
    model_revision: str
    playbook_revision: str
    expected_revision: int = Field(ge=0)
    design_input: DesignInput


class CustomDeviceInput(StrictModel):
    request_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    group_name: str | None = Field(default=None, min_length=1, max_length=200)
    group_revision: str | None = None
    input_W: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_group_or_power(self):
        if self.group_name is not None:
            if not self.group_revision:
                raise ValueError("group_revision is required with group_name")
        elif self.input_W is None or self.group_revision is not None:
            raise ValueError("Choose a load group or provide known input_W")
        return self


class QuestionnaireModelConflict(ValueError):
    def __init__(self, current_model_revision: str):
        self.current_model_revision = current_model_revision
        super().__init__("IFC model revision changed. Reload the template for the current IFC model.")


def _check_model_revision(model: SpatialIndex, model_revision: str) -> None:
    if require_id(model_revision, "model_revision") != model.revision:
        raise QuestionnaireModelConflict(model.revision)


def _checklist_template(project_id: str, *, model: SpatialIndex, catalog: ChecklistCatalog, store: Any) -> dict[str, Any]:
    require_id(project_id, "project_id")
    saved = store.read(project_id)
    submission = saved["submission"] if saved else {}
    spaces = []
    for space_id, floor in model.rooms.items():
        name = model.space_names.get(space_id, space_id)
        profile = suggest_profile(name)
        spaces.append({
            "space_id": space_id,
            "name": name,
            "storey_id": floor,
            "storey_name": model.storey_names.get(floor, floor),
            "room_type": profile,
            "recommended_load_class_ids": catalog.recommendations.get(profile, []),
        })
    return {
        "project_id": project_id,
        "template_id": CHECKLIST_TEMPLATE_ID,
        "template_version": CHECKLIST_TEMPLATE_VERSION,
        "template_revision": catalog.template_revision,
        "model_revision": model.revision,
        "playbook_revision": catalog.revision,
        "expected_revision": saved["questionnaire_revision"] if saved else 0,
        "scope_options": ["building", "room"],
        "spaces": spaces,
        "room_groups": [
            {
                "room_type": key,
                "label": PROFILES[key]["label"],
                "space_ids": [s["space_id"] for s in spaces if s["room_type"] == key],
            }
            for key in PROFILES if key != "building" and any(s["room_type"] == key for s in spaces)
        ],
        "building_recommended_load_class_ids": catalog.recommendations["building"],
        "catalog": list(catalog.items.values()),
        "selection_semantics": {
            "true": "needed",
            "false": "not_needed",
            "null_or_omitted": "unanswered",
            "submit": "replace entire checklist; preserve selections from all scopes",
        },
        "saved_selections": submission.get("selections", []),
        "saved_playbook_revision": submission.get("playbook_revision"),
        "saved_model_revision": submission.get("model_revision"),
        "has_legacy_answers": bool(submission.get("answers")),
        "warnings": [{"code": "LEGACY_ANSWERS_REQUIRE_MIGRATION"}] if submission.get("answers") else [],
    }


def _form_template(project_id: str, *, model: SpatialIndex, catalog: ChecklistCatalog, store: Any) -> dict[str, Any]:
    result = _checklist_template(project_id, model=model, catalog=catalog, store=store)
    saved = store.read(project_id)
    options = {room_type: room_device_options(catalog, room_type) for room_type in catalog.recommendations}
    choices = [
        {
            "space_id": s["space_id"],
            "room_type": s["room_type"],
            "air_conditioners": int(s["room_type"] in {"bedroom", "living"} and "air_conditioners" in options[s["room_type"]]),
            "water_heater": "water_heater" in options[s["room_type"]],
        }
        for s in result["spaces"]
    ]
    result.update({
        "room_device_options": options,
        "sections": ["automatic", "rooms", "shared_water_heaters", "kitchens", "pump", "other_devices", "custom_devices"],
        "suggested_design_input": {
            "rooms": choices,
            "shared_water_heaters_by_storey": False,
            "kitchens": [
                {"space_id": s["space_id"], "stove": "electric", "burners": "double", "oven": False, "fridge": True}
                for s in result["spaces"] if s["room_type"] == "kitchen"
            ],
            "pump": True,
            "washing_machine_space_id": None,
            "custom_devices": [],
        },
        "saved_design_input": saved["submission"].get("design_input") if saved else None,
        "automatic": {
            "types": ["lighting", "switches", "receptacles", "ventilation"],
            "status": "planned_in_phase_2",
            "quantity": None,
        },
        "missing_catalog_options": ["dishwasher", "microwave"],
        "custom_device_fields": ["request_id", "name", "group_name", "group_revision"],
        "custom_device_optional_fields": ["input_W"],
        "load_groups": load_groups(catalog),
        "warnings": result["warnings"] + [{"code": "SUGGESTIONS_REQUIRE_SUBMISSION"}],
    })
    return result


def questionnaire_get_template(
    project_id: str,
    building_type: str,
    model_revision: str,
    *,
    model: SpatialIndex,
    catalog: ChecklistCatalog | None = None,
    store: Any = None,
) -> dict[str, Any]:
    """Tạo mẫu nhập tay hoặc bảng tick nếu có catalog Playbook."""
    require_id(project_id, "project_id")
    if building_type != BUILDING_TYPE:
        raise ValueError(f"Unsupported building_type: {building_type!r}")
    _check_model_revision(model, model_revision)
    if catalog is not None:
        if store is None:
            raise ValueError("Checklist template requires a questionnaire store")
        return _form_template(project_id, model=model, catalog=catalog, store=store)

    questions = []
    for q_id, (prompt, intent) in QUESTIONS.items():
        follow_up_fields = (["space_id", "placement_hint", "quantity"] if intent == "supply_point"
                            else ["space_id", "quantity", "placement_hint"])
        if intent == "equipment":
            follow_up_fields.extend(POWER_FIELDS)
        questions.append({
            "q_id": q_id,
            "text": prompt,
            "type": "single_select",
            "options": [{"id": key, "label": label} for key, label in SELECTIONS.items()],
            "follow_up_when": "needed",
            "follow_up_fields": follow_up_fields,
        })

    spaces = [
        {
            "space_id": space_id,
            "name": model.space_names.get(space_id, space_id),
            "storey_id": storey_id,
            "suggested_room_type": suggest_profile(model.space_names.get(space_id, "")),
        }
        for space_id, storey_id in sorted(model.rooms.items())
    ]
    return {
        "project_id": project_id,
        "template_id": TEMPLATE_ID,
        "template_version": TEMPLATE_VERSION,
        "model_revision": model.revision,
        "questions": questions,
        "question_profiles": PROFILES,
        "spaces": spaces,
        "storeys": [
            {"storey_id": storey_id, "name": model.storey_names.get(storey_id, storey_id)}
            for storey_id in sorted(model.floors)
        ],
    }


def choose(prompt: str, options: list[tuple[Any, str]], default: Any = None) -> Any:
    for number, (_, label) in enumerate(options, 1):
        print(f"  {number}. {label}")
    default_number = next((number for number, (key, _) in enumerate(options, 1)
                           if key == default), None)
    hint = f" [{default_number}]" if default_number is not None else ""
    while True:
        choice = input(f"{prompt}{hint}: ").strip()
        if not choice and default_number is not None:
            return default
        if choice.isdecimal() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1][0]
        print("Nhập số trong danh sách.")


def optional_value(label: str, default: Any = None, convert: Any = str, *,
                   minimum: float | None = None, maximum: float | None = None,
                   exclusive_minimum: bool = False) -> Any:
    """Cho phép bỏ trống, giữ nguyên hoặc sửa giá trị tùy chọn."""
    print(f"\n{label}")
    options = [("enter", "Nhập giá trị"), ("unknown", "Chưa biết / chưa cung cấp")]
    if default is not None:
        options.append(("keep", f"Giữ giá trị hiện tại: {default}"))
    action = choose("Chọn", options, "keep" if default is not None else "unknown")
    while True:
        if action == "unknown":
            return None
        if action == "keep":
            return default
        value = input(f"{label} (Enter = chưa biết): ").strip()
        if not value:
            return None
        try:
            result = convert(value)
            if convert is not str:
                if not math.isfinite(result):
                    raise ValueError("Phải là số hữu hạn.")
                if minimum is not None and result < minimum:
                    raise ValueError(f"Giá trị phải >= {minimum}.")
                if maximum is not None and result > maximum:
                    raise ValueError(f"Giá trị phải <= {maximum}.")
                if exclusive_minimum and result == minimum:
                    raise ValueError("PF phải > 0 và <= 1.")
            return result
        except (ValueError, OverflowError):
            print("Giá trị không hợp lệ. " + (
                "Số lượng phải là số nguyên >= 1." if convert is int else
                "PF phải > 0 và <= 1." if maximum == 1 else
                "Nhập số hữu hạn không âm, không kèm đơn vị."
            ))
            retry = [("enter", "Nhập lại"), ("unknown", "Đánh dấu chưa biết và tiếp tục")]
            if default is not None:
                retry.append(("keep", f"Giữ giá trị hiện tại: {default}"))
            action = choose("Cách xử lý", retry)


def location_text(default: str | None = None) -> str | None:
    hint = f" [{default}]" if default else ""
    value = input(f"Vị trí mong muốn (ví dụ: gần bàn làm việc){hint}; Enter = giữ/bỏ qua, - = xóa: ").strip()
    return None if value == "-" else value or default


def select_scope(rooms: dict[str, str], existing: dict | None = None) -> tuple[str, str, str]:
    existing = existing or {}
    scope = choose("Chọn phạm vi", [("building", "Toàn công trình"), ("room", "Theo phòng")],
                   existing.get("scope_type", "room") if existing else None)
    if scope == "building":
        return "building", "", "building"
    old_id = existing.get("space_id")
    old_profile = existing.get("room_type")
    overrides = {old_id: old_profile} if old_id in rooms and old_profile in PROFILES and old_profile != "building" else {}
    groups = group_rooms(rooms, overrides)
    options = [(key, f"{PROFILES[key]['label']} ({len(members)} phòng)") for key, members in groups.items()]
    options.extend([("__all__", "Tìm phòng / đổi nhóm chức năng"), ("__unassigned__", "Chưa chọn phòng cụ thể")])
    default = overrides.get(old_id, suggest_profile(rooms.get(old_id, ""))) if old_id in rooms else "__unassigned__"
    group = choose("Chọn nhóm chức năng", options, default if existing else None)
    if group == "__unassigned__":
        space_id = ""
    elif group == "__all__":
        if not rooms:
            print("IFC chưa có phòng; ghi nhận nhu cầu chưa gán phòng.")
            space_id = ""
        else:
            space_id = choose("Chọn phòng cụ thể", list(rooms.items()), old_id)
    else:
        space_id = choose("Chọn phòng cụ thể", groups[group], old_id)
        return "room", space_id, group
    profile = choose("Chọn chức năng phòng", [(key, row["label"]) for key, row in PROFILES.items() if key != "building"],
                     overrides.get(space_id, suggest_profile(rooms.get(space_id, ""))))
    return "room", space_id, profile


def read_answer(rooms: dict[str, str], questions: dict[str, dict], existing: dict | None = None) -> dict[str, Any]:
    existing = existing or {}
    print(
        "\nCác trường số có lựa chọn nhập mới hoặc chưa biết. Vị trí nhập văn bản trực tiếp. "
        "Khi sửa, Enter tại menu giữ giá trị cũ; chọn Chưa biết để xóa."
    )
    scope_type, space_id, profile = select_scope(rooms, existing)
    visible = list(question_ids(profile))
    if existing.get("q_id") in questions and existing["q_id"] not in visible:
        visible.append(existing["q_id"])
    if profile != "building":
        visible.append("__more__")
    choices = [(key, questions[key]["text"]) if key != "__more__" else (key, "Nhu cầu khác: xem các câu hỏi còn lại") for key in visible]
    q_id = choose("Chọn câu hỏi", choices, existing.get("q_id"))
    if q_id == "__more__":
        q_id = choose("Chọn nhu cầu bổ sung", [(key, questions[key]["text"]) for key in question_ids("other")])
    question = questions[q_id]
    selected = choose(
        "Câu trả lời", [(option["id"], option["label"]) for option in question["options"]],
        existing.get("selected"),
    )
    answer = {
        "answer_item_id": existing.get("answer_item_id") or str(uuid4()),
        "space_id": space_id or None,
        "scope_type": scope_type,
        "room_type": profile,
        "q_id": q_id,
        "selected": selected,
    }
    if selected != "needed":
        return answer

    old_values = existing if q_id == existing.get("q_id") else {}
    if q_id == "general_receptacle":
        answer["placement_hint"] = location_text(old_values.get("placement_hint"))
    quantity_label = "Số lượng điểm ổ cắm / cấp điện" if q_id == "general_receptacle" else "Số lượng thiết bị"
    answer["quantity"] = optional_value(quantity_label, old_values.get("quantity"), int, minimum=1)
    if "input_W" in question["follow_up_fields"]:
        for field, label in (("input_W", "Công suất mỗi thiết bị (W)"),
                             ("input_VA", "Công suất biểu kiến mỗi thiết bị (VA)"),
                             ("power_factor", "Hệ số công suất (PF)")):
            answer[field] = optional_value(
                label, old_values.get(field), float, minimum=0,
                maximum=1 if field == "power_factor" else None,
                exclusive_minimum=field == "power_factor",
            )
        if answer["input_W"] is not None or answer["input_VA"] is not None:
            answer["power_source"] = optional_value(
                "Nguồn công suất (có thể bổ sung sau)", old_values.get("power_source"),
            )
        if answer["power_factor"] is not None:
            answer["pf_source"] = optional_value("Nguồn PF", old_values.get("pf_source"))
    if q_id != "general_receptacle":
        answer["placement_hint"] = location_text(old_values.get("placement_hint"))
    return answer
