"""Tool #2: questionnaire_submit.

Xử lý và kiểm tra câu trả lời; lưu revision vào PostgreSQL;
quản lý thiết bị tùy chỉnh trong Playbook và mở rộng bảng tick sang nhu cầu.
"""

import argparse
import json
import math
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from postgrest.exceptions import APIError

from database.project_store import require_id
from ifc_engine.reader import SpatialIndex, read_spaces
from supabase import Client
from tools.calc_preliminary_load import validate_power_values
from tools.phase1.questionnaire_get_load_context import (
    _derive_context,
    _public_context,
    questionnaire_get_load_context,
)
from tools.phase1.questionnaire_get_template import (
    ANSWER_FIELDS,
    BUILDING_TYPE,
    CHECKLIST_TEMPLATE_ID,
    CHECKLIST_TEMPLATE_VERSION,
    POWER_FIELDS,
    PROFILES,
    QUESTIONS,
    SELECTIONS,
    TEMPLATE_ID,
    TEMPLATE_VERSION,
    ChecklistCatalog,
    DesignInput,
    FormSubmission,
    _check_model_revision,
    choose,
    load_groups,
    questionnaire_get_template,
    read_answer,
    room_device_options,
    suggest_profile,
)


class QuestionnaireRevisionConflict(ValueError):
    def __init__(self, expected: int, current: int):
        self.current_revision = current
        super().__init__(
            f"Expected questionnaire revision {expected}, current revision is {current}"
        )


class CustomDeviceConflict(ValueError):
    pass


class QuestionnaireStore:
    def __init__(self, client: Client):
        self.client = client

    def read(self, project_id: str, revision: int | None = None) -> dict | None:
        query = (
            self.client.table("phase1_questionnaire_revisions")
            .select("snapshot")
            .eq("project_id", project_id)
        )
        if revision is not None:
            query = query.eq("revision", revision)
        rows = query.order("revision", desc=True).limit(1).execute().data
        return rows[0]["snapshot"] if rows else None

    def save(
        self,
        project_id: str,
        expected_revision: int,
        submission: dict,
        build_context: Callable[[dict[str, str]], dict],
    ) -> dict:
        latest = self.read(project_id)
        previous_ids = latest["context"]["requirement_ids"] if latest else {}
        result = (
            self.client.rpc(
                "phase1_save_questionnaire",
                {
                    "p_project_id": project_id,
                    "p_expected_revision": expected_revision,
                    "p_submission": submission,
                    "p_context": build_context(previous_ids),
                },
            )
            .execute()
            .data
        )
        if "conflict_revision" in result:
            raise QuestionnaireRevisionConflict(expected_revision, result["conflict_revision"])
        return result


class PlaybookStore:
    def __init__(self, questionnaire_store: QuestionnaireStore):
        self.client = questionnaire_store.client

    def catalog(self, project_id: str, base: ChecklistCatalog) -> ChecklistCatalog:
        items = dict(base.items)
        query = (
            self.client.table("phase1_custom_devices").select("item").eq("project_id", project_id)
        )
        offset = 0
        page_size = 100
        while True:
            rows = query.order("request_id").range(offset, offset + page_size - 1).execute().data
            for row in rows:
                item = row["item"]
                items[item["load_class_id"]] = item
            if len(rows) < page_size:
                break
            offset += page_size
        revision = sha256(
            json.dumps(items, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return ChecklistCatalog(revision, items, base.recommendations, base.template_revision)

    def _find_custom_device(self, project_id: str, request_id: str) -> dict | None:
        rows = (
            self.client.table("phase1_custom_devices")
            .select("payload,item")
            .eq("project_id", project_id)
            .eq("request_id", request_id)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None

    @staticmethod
    def _match_custom_device(saved: dict, payload: dict) -> dict:
        if saved["payload"] != payload:
            raise CustomDeviceConflict("request_id already used for another device payload")
        return saved["item"]

    def create_custom_device(
        self,
        project_id: str,
        request_id: str,
        name: str,
        input_W: float | None = None,
        group_name: str | None = None,
        group_revision: str | None = None,
        *,
        catalog: ChecklistCatalog | None = None,
    ) -> dict[str, Any]:
        require_id(project_id, "project_id")
        require_id(request_id, "request_id")
        require_id(name, "name")
        if len(name.strip()) > 200 or len(request_id) > 200:
            raise ValueError("Name or request_id too long")
        if input_W is not None and (
            type(input_W) not in (int, float) or not math.isfinite(input_W) or input_W <= 0
        ):
            raise ValueError("input_W must be a finite positive number")
        if group_name is not None:
            require_id(group_name, "group_name")
            group_name = group_name.strip()
            require_id(group_revision, "group_revision")
        elif group_revision is not None or input_W is None:
            raise ValueError("Select group_name and group_revision, or supply input_W")
        payload = {"name": name.strip(), "input_W": input_W}
        if group_name is not None:
            payload.update(group_name=group_name, group_revision=group_revision)
        saved = self._find_custom_device(project_id, request_id)
        if saved:
            return self._match_custom_device(saved, payload)
        group = None
        if group_name is not None:
            if catalog is None:
                raise ValueError("Playbook catalog is required for group selection")
            group = next((g for g in load_groups(catalog) if g["group_name"] == group_name), None)
            if group is None:
                raise ValueError("Unknown load group; select from load-groups")
            if group["group_revision"] != group_revision:
                raise CustomDeviceConflict(
                    "Load group changed; reload load-groups before creating device"
                )
            if group["intent"] is None:
                raise ValueError("Load group contains incompatible intents")
            if input_W is not None and group["intent"] != "equipment":
                raise ValueError(
                    "Consumption watts apply only to equipment, not supply points or generation"
                )
        key = "CUSTOM-" + str(uuid4())
        item = {
            "load_class_id": key,
            "label": payload["name"],
            "group": "Thiết bị người dùng bổ sung",
            "intent": "equipment",
            "input_W": input_W,
            "input_VA": None,
            "power_factor": None,
            "reference_power_W": {"min": input_W, "max": input_W},
            "demand_factor": None,
            "basis": "Công suất điện đầu vào do người dùng khai báo",
            "unit": "Thiết bị",
            "notes": None,
            "rule_source": None,
            "verification_status": "Người dùng khai báo",
            "source": {
                "kind": "user_declared",
                "project_id": project_id,
                "request_id": request_id,
                "load_class_id": key,
            },
        }
        if group is not None:
            item.update(
                group=group_name,
                intent=group["intent"],
                reference_power_W=group["reference_power_W"],
                basis="Khoảng tham khảo tổng hợp từ nhóm phụ tải trong Playbook",
                verification_status=(
                    "Người dùng khai báo công suất thực tế"
                    if input_W is not None
                    else "Tham khảo theo nhóm; chưa xác nhận công suất thiết bị"
                ),
                group_snapshot=group,
            )
            item["source"]["group_revision"] = group_revision
            item["source"]["range_origin"] = "playbook_load_group"
        rows = (
            self.client.table("phase1_custom_devices")
            .upsert(
                {
                    "project_id": project_id,
                    "request_id": request_id,
                    "load_class_id": key,
                    "payload": payload,
                    "item": item,
                },
                on_conflict="project_id,request_id",
                ignore_duplicates=True,
            )
            .execute()
            .data
        )
        if rows:
            return rows[0]["item"]
        saved = self._find_custom_device(project_id, request_id)
        if saved is None:
            raise RuntimeError("Không đọc được thiết bị tùy chỉnh vừa lưu.")
        return self._match_custom_device(saved, payload)


def _validated_answers(answers: list[dict], model: SpatialIndex) -> list[dict]:
    if not isinstance(answers, list):
        raise ValueError("answers must be a list")

    validated = []
    item_ids = set()
    group_selections = {}
    for index, answer in enumerate(answers):
        try:
            if not isinstance(answer, dict) or answer.keys() - ANSWER_FIELDS:
                raise ValueError("Expected an answer object containing only questionnaire fields")
            answer = {
                key: (None if isinstance(value, str) and not value.strip() else value)
                if key in {"placement_hint", "power_source", "pf_source"}
                else value
                for key, value in answer.items()
            }
            item_id = require_id(answer.get("answer_item_id"), "answer_item_id")
            if item_id in item_ids:
                raise ValueError(f"Duplicate answer_item_id: {item_id}")
            item_ids.add(item_id)

            q_id = require_id(answer.get("q_id"), "q_id")
            if q_id not in QUESTIONS:
                raise ValueError(f"Unknown q_id: {q_id}")
            selected = require_id(answer.get("selected"), "selected")
            if selected not in SELECTIONS:
                raise ValueError(f"Unknown selected option: {selected}")

            scope = answer.get("scope_type", "room")
            if scope not in {"room", "building"}:
                raise ValueError("Unknown scope_type")
            if answer.get("room_type") is not None and answer["room_type"] not in PROFILES:
                raise ValueError("Unknown room_type")
            if scope == "building" and (
                answer.get("space_id") is not None or q_id not in PROFILES["building"]["questions"]
            ):
                raise ValueError("Invalid building requirement")
            space_id = answer.get("space_id")
            if space_id is not None:
                require_id(space_id, "space_id")
                if space_id not in model.rooms:
                    raise ValueError(f"Unknown IFC space_id: {space_id}")
                if group_selections.setdefault((space_id, q_id), selected) != selected:
                    raise ValueError(f"Conflicting selections for {q_id} in {space_id}")

            count = answer.get("quantity")
            if count is not None and (type(count) is not int or count < 1):
                raise ValueError("quantity must be a positive integer")
            for field in ("placement_hint", "power_source", "pf_source"):
                if answer.get(field) is not None:
                    require_id(answer[field], field)
            if QUESTIONS[q_id][1] == "supply_point" and any(
                answer.get(field) is not None for field in POWER_FIELDS
            ):
                raise ValueError("Power fields apply to equipment, not to supply points")
            validate_power_values(answer)
        except ValueError as error:
            raise ValueError(f"answers[{index}]: {error}") from error

        validated.append({key: value for key, value in answer.items() if value is not None})

    return sorted(validated, key=lambda ans: ans["answer_item_id"])


def expand_design(
    design: DesignInput, model: SpatialIndex, catalog: ChecklistCatalog
) -> tuple[list[dict], list[dict]]:
    by_room = {row.space_id: row for row in design.rooms}
    if len(by_room) != len(design.rooms) or set(by_room) != set(model.rooms):
        raise ValueError("rooms must contain every current IFC space exactly once")
    profiles = {}
    for key, choice in by_room.items():
        profile = choice.room_type or suggest_profile(model.space_names.get(key, key))
        if profile not in PROFILES or profile == "building":
            raise ValueError("Invalid room_type")
        profiles[key] = profile
    selections, automatic, heaters = [], [], {}

    def add(key, room=None, count=1, hint=None, group=None, served=None):
        if room is not None and room not in model.rooms:
            raise ValueError("Unknown IFC room")
        if key not in catalog.items:
            raise ValueError("Unknown catalog device")
        selections.append(
            {
                "scope_type": "room" if room is not None else "building",
                "space_id": room,
                "load_class_id": key,
                "checked": True,
                "quantity": count,
                "placement_hint": hint,
                "group_id": group,
                "served_space_ids": served or [],
            }
        )

    for room, choice in by_room.items():
        options = room_device_options(catalog, profiles[room])
        if choice.air_conditioners and "air_conditioners" not in options:
            raise ValueError(
                "Điều hòa không nằm trong lựa chọn của loại phòng này. Dùng Thiết bị tùy chỉnh nếu cần."
            )
        if choice.air_conditioners:
            add("TẢI-006", room, choice.air_conditioners)
        if choice.water_heater:
            if "water_heater" not in options:
                raise ValueError("Bình nước nóng không nằm trong lựa chọn của loại phòng này.")
            if design.shared_water_heaters_by_storey:
                floor = model.rooms[room]
                if floor is None:
                    raise ValueError("Shared heater requires an IFC storey")
                heaters.setdefault(floor, []).append(room)
            else:
                add("TẢI-007", room)
        outlet = {"bedroom": "TẢI-003", "living": "TẢI-002", "kitchen": "TẢI-004"}.get(
            profiles[room]
        )
        automatic.append(
            {
                "space_id": room,
                "room_type": profiles[room],
                "quantity": None,
                "types": ["lighting", "switches", "receptacles"]
                + (["ventilation"] if profiles[room] == "bathroom" else []),
                "load_class_ids": {
                    "lighting": "TẢI-001",
                    "switches": None,
                    "receptacles": outlet,
                    "ventilation": "TẢI-017" if profiles[room] == "bathroom" else None,
                },
                "status": "requires_phase_2_rules_and_placement",
            }
        )

    for floor, rooms in sorted(heaters.items()):
        add(
            "TẢI-007",
            group="shared-heater:" + floor,
            served=sorted(rooms),
            hint="Một bình dùng chung cho các WC đã chọn cùng tầng; vị trí lắp cần xác định",
        )

    seen_kitchens = set()
    for kitchen in design.kitchens:
        if kitchen.space_id not in model.rooms or kitchen.space_id in seen_kitchens:
            raise ValueError("Unknown or duplicate kitchen room")
        seen_kitchens.add(kitchen.space_id)
        if profiles[kitchen.space_id] != "kitchen":
            raise ValueError("Kitchen controls require a kitchen room")
        if kitchen.stove == "gas":
            if kitchen.burners is not None or kitchen.oven:
                raise ValueError("Gas stove must have burners=null and oven=false")
        else:
            if kitchen.burners is None:
                raise ValueError("Electric stove requires single/double selection")
            add("TẢI-008", kitchen.space_id, hint="Bếp " + kitchen.burners)
            if kitchen.oven:
                add("TẢI-009", kitchen.space_id)
        if kitchen.fridge:
            add("TẢI-014", kitchen.space_id)
        for key in (kitchen.dishwasher_catalog_id, kitchen.microwave_catalog_id):
            if key:
                if (
                    key not in catalog.items
                    or catalog.items[key]["source"]["kind"] != "user_declared"
                ):
                    raise ValueError("This appliance needs a custom catalog entry")
                add(key, kitchen.space_id)

    if design.pump:
        add(
            "TẢI-012",
            hint="Ưu tiên tầng thấp nhất, sát tường ngoài; Phase 2 xác định từ hình học IFC",
        )
    if design.washing_machine_space_id is not None:
        add("TẢI-010", design.washing_machine_space_id)
    for device in design.custom_devices:
        if (
            device.load_class_id not in catalog.items
            or catalog.items[device.load_class_id]["source"]["kind"] != "user_declared"
        ):
            raise ValueError("custom_devices must reference this project's custom Playbook entries")
        add(device.load_class_id, device.space_id, device.quantity)
    return selections, automatic


def questionnaire_submit(
    project_id: str,
    template_id: str | None = None,
    template_version: str | None = None,
    model_revision: str | None = None,
    expected_revision: int | None = None,
    answers: list[dict] | None = None,
    *,
    model: SpatialIndex,
    store: QuestionnaireStore,
    submission: FormSubmission | None = None,
    catalog: ChecklistCatalog | None = None,
) -> dict:
    """Validate and persist one manual/checklist questionnaire revision atomically.

    Manual callers pass template_id/version, IFC model_revision, expected_revision
    (0 for first submission) and answer objects with stable answer_item_id, q_id,
    selected, optional room GlobalId, quantity and sourced consumption W/VA/PF.
    Supply-point ratings cannot be declared as consumption. Checklist callers
    instead provide FormSubmission and the pinned ChecklistCatalog. Do not mix
    both forms. The store rejects stale revisions and preserves requirement IDs.
    Returns status, questionnaire_revision, derived_load_context and warnings;
    unknown power, quantity and unallocated building loads stay explicit.
    Does not place devices or approve a design.
    """
    if submission is not None:
        if catalog is None:
            raise ValueError("Checklist submission requires a Playbook catalog")
        if any(
            value is not None
            for value in (template_id, template_version, model_revision, expected_revision, answers)
        ):
            raise ValueError("Submit either manual answers or a checklist")
        selections, automatic = expand_design(submission.design_input, model, catalog)
        return _save_checklist(
            project_id,
            **submission.model_dump(exclude={"design_input"}),
            selections=selections,
            model=model,
            catalog=catalog,
            store=store,
            design_input=submission.design_input.model_dump(),
            automatic_requirements=automatic,
        )
    if catalog is not None or answers is None:
        raise ValueError("Manual submission requires answers and no checklist catalog")
    require_id(project_id, "project_id")
    if (template_id, template_version) != (TEMPLATE_ID, TEMPLATE_VERSION):
        raise ValueError("Unknown questionnaire template or template version")
    _check_model_revision(model, model_revision)
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a non-negative integer")

    latest = store.read(project_id)
    if latest and "selections" in latest["submission"]:
        raise ValueError("This project uses the checklist; submit through checklist-submit")
    validated = _validated_answers(answers, model)
    snapshot = store.save(
        project_id,
        expected_revision,
        {
            "template_id": template_id,
            "template_version": template_version,
            "model_revision": model_revision,
            "space_ids": sorted(model.rooms),
            "answers": validated,
        },
        lambda previous_ids: _derive_context(validated, previous_ids),
    )
    context = _public_context(project_id, snapshot)
    return {
        "status": "saved",
        "questionnaire_revision": context["questionnaire_revision"],
        "warnings": context["warnings"],
        "derived_load_context": context,
    }


def _save_checklist(
    project_id: str,
    template_id: str,
    template_version: str,
    template_revision: str,
    model_revision: str,
    playbook_revision: str,
    expected_revision: int,
    selections: list[dict],
    *,
    model: SpatialIndex,
    catalog: ChecklistCatalog,
    store: QuestionnaireStore,
    design_input: dict | None = None,
    automatic_requirements: list[dict] | None = None,
) -> dict:
    require_id(project_id, "project_id")
    if (template_id, template_version, template_revision) != (
        CHECKLIST_TEMPLATE_ID,
        CHECKLIST_TEMPLATE_VERSION,
        catalog.template_revision,
    ):
        raise ValueError("Checklist template changed; reload template")
    _check_model_revision(model, model_revision)
    if playbook_revision != catalog.revision:
        raise ValueError("Playbook revision changed; reload template")
    if (
        type(expected_revision) is not int
        or expected_revision < 0
        or not isinstance(selections, list)
    ):
        raise ValueError("Invalid expected_revision or selections")
    rows, seen = [], set()
    for selection in selections:
        if not isinstance(selection, dict) or selection.keys() - {
            "scope_type",
            "space_id",
            "load_class_id",
            "checked",
            "quantity",
            "placement_hint",
            "group_id",
            "served_space_ids",
        }:
            raise ValueError(
                "Only checkbox selection fields are accepted; power comes from Playbook"
            )
        scope, room, key = (
            selection.get("scope_type"),
            selection.get("space_id"),
            selection.get("load_class_id"),
        )
        if (
            scope not in {"building", "room"}
            or (scope == "room" and room not in model.rooms)
            or (scope == "building" and room is not None)
        ):
            raise ValueError("Invalid scope or IFC space_id")
        if key not in catalog.items:
            raise ValueError("Unknown load_class_id")
        checked, count = selection.get("checked"), selection.get("quantity")
        if checked is not None and type(checked) is not bool:
            raise ValueError("checked must be true, false or null")
        if count is not None and (type(count) is not int or count < 1):
            raise ValueError("quantity must be a positive integer or null")
        hint = selection.get("placement_hint")
        if hint is not None:
            require_id(hint, "placement_hint")
        item_id = str(
            uuid5(
                NAMESPACE_URL,
                json.dumps(
                    [project_id, scope, room, key, selection.get("group_id")], ensure_ascii=False
                ),
            )
        )
        if item_id in seen:
            raise ValueError("Duplicate selection for scope/room/load class")
        seen.add(item_id)
        rows.append(
            {
                "scope_type": scope,
                "space_id": room,
                "load_class_id": key,
                "checked": checked,
                "quantity": count,
                "placement_hint": hint,
                "answer_item_id": item_id,
                "group_id": selection.get("group_id"),
                "served_space_ids": selection.get("served_space_ids", []),
            }
        )
    rows.sort(key=lambda row: row["answer_item_id"])

    def build(previous_ids):
        context = {
            "requirements": [],
            "unresolved_requirements": [],
            "warnings": [],
            "requirement_ids": {},
        }
        for row in rows:
            item_id = row["answer_item_id"]
            requirement_id = previous_ids.get(item_id) or "req-" + str(
                uuid5(NAMESPACE_URL, item_id)
            )
            context["requirement_ids"][item_id] = requirement_id
            if row["checked"] is not True:
                continue
            item = catalog.items[row["load_class_id"]]
            req = {
                **row,
                "requirement_id": requirement_id,
                "source_answer_item_id": item_id,
                "intent": item["intent"],
                "input_W": item["input_W"],
                "input_VA": None,
                "power_factor": None,
                "power_source": json.dumps(item["source"], ensure_ascii=False)
                if item["input_W"] is not None
                else None,
                "playbook_revision": catalog.revision,
                "catalog_snapshot": item,
            }
            context[
                "unresolved_requirements" if row["space_id"] is None else "requirements"
            ].append(req)
            codes = []
            if row["space_id"] is None:
                codes.append("BUILDING_REQUIREMENT_NEEDS_ALLOCATION")
            if row["quantity"] is None:
                codes.append("QUANTITY_TO_BE_PLANNED")
            if item["intent"] == "equipment" and item["input_W"] is None:
                codes.append("PLAYBOOK_POWER_REQUIRES_SELECTION")
            for code in codes:
                context["warnings"].append(
                    {"code": code, "requirement_id": requirement_id, "space_id": row["space_id"]}
                )
        context["automatic_requirements"] = automatic_requirements or []
        return context

    latest = store.read(project_id)
    if latest and latest["submission"].get("answers"):
        raise ValueError(
            "Existing manual answers require migration; use a new project for the checklist"
        )
    submission = {
        "template_id": CHECKLIST_TEMPLATE_ID,
        "template_version": CHECKLIST_TEMPLATE_VERSION,
        "template_revision": template_revision,
        "model_revision": model.revision,
        "playbook_revision": catalog.revision,
        "space_ids": sorted(model.rooms),
        "answers": [],
        "selections": rows,
        "design_input": design_input,
    }
    saved = store.save(project_id, expected_revision, submission, build)
    result = _public_context(project_id, saved)
    return {
        "status": "saved",
        "questionnaire_revision": saved["questionnaire_revision"],
        "warnings": result["warnings"],
        "derived_load_context": result,
    }


def calculation_playbook(catalog: ChecklistCatalog) -> Any:
    """Chuyển catalog đã gộp thiết bị tùy chỉnh sang định dạng của tool #14."""
    from tools.calc_preliminary_load import LoadClass, Playbook

    return Playbook(
        catalog.revision,
        {
            key: LoadClass(
                key,
                item["label"],
                item["demand_factor"],
                "receptacle_circuit" if item["intent"] == "supply_point" else "equipment_scenario",
                item["rule_source"] or "Chưa có quy tắc nhu cầu cho thiết bị tự thêm",
            )
            for key, item in catalog.items.items()
        },
    )


def run_questionnaire(project_id: str, model: SpatialIndex, store: QuestionnaireStore) -> None:
    template = questionnaire_get_template(project_id, BUILDING_TYPE, model.revision, model=model)
    storeys = {row["storey_id"]: row["name"] for row in template["storeys"]}
    rooms = {}
    for room in template["spaces"]:
        storey = storeys.get(room["storey_id"])
        rooms[room["space_id"]] = f"{room['name']} ({storey})" if storey else room["name"]
    questions = {row["q_id"]: row for row in template["questions"]}
    saved = questionnaire_get_load_context(project_id, store=store, include_answers=True)
    revision = saved["questionnaire_revision"] or 0
    answers = {row["answer_item_id"]: row for row in saved.get("answers", [])}
    print(f"Dự án {project_id} · revision {revision} · {len(answers)} câu trả lời đã lưu")
    if revision and saved["model_revision"] != model.revision:
        print("Phiên bản IFC đã đổi. Kiểm tra lại phòng của các câu trả lời trước khi lưu.")

    while True:
        print()
        action = choose(
            "Thao tác",
            [
                ("add", "Thêm câu trả lời"),
                ("edit", "Sửa câu trả lời"),
                ("save", "Lưu và in kết quả"),
                ("quit", "Thoát"),
            ],
            "add" if not answers else None,
        )
        if action == "quit":
            return
        if action == "save":
            try:
                result = questionnaire_submit(
                    project_id,
                    template["template_id"],
                    template["template_version"],
                    model.revision,
                    revision,
                    list(answers.values()),
                    model=model,
                    store=store,
                )
            except QuestionnaireRevisionConflict as error:
                print(
                    f"Phiên bản đã đổi thành {error.current_revision}. Chạy lại lệnh để tải bản mới."
                )
                return
            except ValueError as error:
                print(f"Cần sửa câu trả lời: {error}")
                continue
            context = questionnaire_get_load_context(
                project_id,
                questionnaire_revision=result["questionnaire_revision"],
                store=store,
            )
            print(json.dumps(context, ensure_ascii=False, indent=2))
            return

        existing = None
        if action == "edit":
            if not answers:
                print("Chưa có câu trả lời để sửa.")
                continue
            options = []
            for item_id, row in answers.items():
                room = rooms.get(row.get("space_id"), row.get("space_id") or "Chưa chọn phòng")
                options.append((item_id, f"{room} · {questions[row['q_id']]['text']}"))
            existing = answers[choose("Chọn dòng cần sửa", options)]
        answer = read_answer(rooms, questions, existing)
        answers[answer["answer_item_id"]] = answer


def main(argv: list[str] | None = None) -> None:
    from config.settings import Settings
    from database.supabase_client import get_supabase_client

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ifc", required=True, type=Path)
    parser.add_argument("--project-id", default="demo-house")
    args = parser.parse_args(argv)
    try:
        store = QuestionnaireStore(get_supabase_client(Settings()))
        run_questionnaire(args.project_id, read_spaces(args.ifc), store)
    except APIError:
        parser.exit(1, "Không truy cập được Supabase. Kiểm tra cấu hình và quyền của Client.\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    except (EOFError, KeyboardInterrupt):
        print("\nĐã thoát bảng hỏi.")


if __name__ == "__main__":
    main()
