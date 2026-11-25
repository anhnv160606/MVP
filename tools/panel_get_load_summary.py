"""T1: tổng hợp tải từ phương án đã duyệt, giữ nguyên revision và dữ liệu chưa biết."""

import hashlib
import json
import math
from dataclasses import replace

from database.project_store import ProjectSnapshot, require_id
from tools.calc_preliminary_load import CONSUMING_KINDS, Playbook, calc_preliminary_load


def _point(value):
    """Chấp nhận tọa độ toàn cục theo mét, không dùng bool hoặc số vô hạn."""
    if not isinstance(value, dict):
        return None
    values = [value.get(axis) for axis in ("x", "y", "z")]
    if any(type(number) not in (int, float) or not math.isfinite(number) for number in values):
        return None
    return dict(zip(("x", "y", "z"), values, strict=True))


def _calculate(snapshot, playbook, rooms, ruleset_id, load_ids=None):
    """Dùng phạm vi zone nội bộ để tính cả công trình bằng lõi Tool 14 hiện có."""
    placements = snapshot.placements
    requirements = snapshot.requirements
    if load_ids is not None:
        placements = [row for row in placements if row["load_id"] in load_ids]
        linked = {row.get("requirement_id") for row in placements}
        requirements = [row for row in requirements if row["requirement_id"] in linked]
    active_ids = {row["load_id"] for row in placements if row.get("status") != "deleted"}
    scenarios = snapshot.scenarios
    if load_ids is not None:
        scenarios = {
            key: [load_id for load_id in ids if load_id in active_ids]
            for key, ids in snapshot.scenarios.items()
        }
    scoped = replace(
        snapshot,
        placements=placements,
        requirements=requirements,
        scenarios=scenarios,
        zones={"panel-summary": {(room, key) for room in rooms for key in playbook.classes}},
    )
    return calc_preliminary_load(
        scoped.plan_id,
        scoped.plan_revision,
        "zone",
        "panel-summary",
        snapshot=scoped,
        playbook=playbook,
        ruleset_id=ruleset_id,
    )


def _totals(calculated):
    keys = (
        "connected_load_W",
        "connected_load_VA",
        "estimated_demand_W",
        "estimated_demand_VA",
        "coverage",
        "demand_breakdown",
        "pending_requirements",
        "warnings",
    )
    return {key: calculated[key] for key in keys}


def _centre(rows):
    loads = [row for row in rows if row["kind"] in CONSUMING_KINDS]
    basis = "connected_VA"
    if any(row[basis] is None for row in loads):
        if loads and all(row["connected_W"] is not None for row in loads):
            basis = "connected_W"
    included, excluded = [], []
    for row in loads:
        point = _point(row.get("position"))
        weight = row[basis]
        if point is None or weight is None:
            excluded.append(row["load_id"])
        else:
            included.append((row["load_id"], point, weight))
    total_weight = sum(weight for _, _, weight in included)
    position = None
    if total_weight > 0:
        position = {
            axis: round(
                sum(point[axis] * weight for _, point, weight in included) / total_weight, 6
            )
            for axis in ("x", "y", "z")
        }
    return {
        "position_m": position,
        "weight_basis": basis,
        "coordinate_system": "model_world",
        "unit": "m",
        "included_device_ids": sorted(key for key, _, _ in included),
        "excluded_device_ids": sorted(excluded),
        "coverage": "partial" if excluded else "complete",
    }


def panel_get_load_summary(
    design_context_id: str,
    scope_type: str = "building",
    scope_id: str | None = None,
    *,
    snapshot: ProjectSnapshot,
    playbook: Playbook,
    source_refs: dict,
    circuit_groups: dict[str, str],
    ruleset_id: str | None = "VN_TOWNHOUSE_PRELIM_V1",
) -> dict:
    """Trả tải tổng, tải theo tầng/nhóm và tâm tải từ snapshot được backend gắn sẵn.

    snapshot dùng hợp đồng Tool 14; position của từng placement là model_world/m.
    source_refs cần plan, version, IFC, questionnaire, Playbook và ruleset revision.
    circuit_groups ánh xạ load_id sang nhóm mạch đã tra từ Playbook.
    Hàm không ghi DB hoặc tự tạo thiết bị từ nhu cầu chưa bố trí.
    """
    require_id(design_context_id, "design_context_id")
    required = (
        "placement_plan_id",
        "placement_version",
        "source_file_id",
        "model_revision",
        "questionnaire_revision",
        "playbook_revision",
        "ruleset_revision",
    )
    for field in required:
        if field not in source_refs or source_refs[field] is None:
            raise ValueError(f"Thiếu revision nguồn: {field}")
    if snapshot.plan_status != "approved":
        raise ValueError("Chỉ tổng hợp phương án bố trí đã duyệt.")
    if (source_refs["placement_plan_id"], str(source_refs["placement_version"])) != (
        snapshot.plan_id,
        str(snapshot.plan_revision),
    ):
        raise ValueError("Phương án nguồn không khớp snapshot.")
    if source_refs["model_revision"] != snapshot.model.revision:
        raise ValueError("Revision IFC không khớp.")
    if source_refs["playbook_revision"] != playbook.revision:
        raise ValueError("Revision Playbook không khớp.")
    if (
        type(source_refs["questionnaire_revision"]) is not int
        or source_refs["questionnaire_revision"] < 1
    ):
        raise ValueError("questionnaire_revision phải là số nguyên dương.")
    if scope_type == "building" and scope_id is None:
        rooms = set(snapshot.model.rooms)
    elif scope_type == "floor" and scope_id in snapshot.model.floors:
        rooms = {room for room, floor in snapshot.model.rooms.items() if floor == scope_id}
    else:
        raise ValueError("Phạm vi phải là building hoặc floor với ID tầng tồn tại.")
    result = _calculate(snapshot, playbook, rooms, ruleset_id)
    rows = result["load_breakdown"]
    warnings = list(result["warnings"])
    missing = []
    floors = sorted({snapshot.model.rooms[room] for room in rooms if snapshot.model.rooms[room]})
    unknown_floor = [row["load_id"] for row in rows if snapshot.model.rooms[row["room_id"]] is None]
    if unknown_floor:
        missing.append({"code": "MISSING_STOREY", "device_ids": unknown_floor})
    by_storey = []
    centres = []
    for floor in floors:
        floor_rooms = {room for room in rooms if snapshot.model.rooms[room] == floor}
        floor_result = _calculate(snapshot, playbook, floor_rooms, ruleset_id)
        floor_rows = floor_result["load_breakdown"]
        groups = {}
        for row in floor_rows:
            if row["kind"] in CONSUMING_KINDS:
                group = circuit_groups.get(row["load_id"], "unclassified")
                require_id(group, "circuit_group")
                groups.setdefault(group, set()).add(row["load_id"])
        group_results = []
        for group, ids in sorted(groups.items()):
            values = _calculate(snapshot, playbook, floor_rooms, ruleset_id, ids)
            group_results.append(
                {"circuit_group": group, "device_ids": sorted(ids), **_totals(values)}
            )
            if group == "unclassified":
                missing.append({"code": "MISSING_CIRCUIT_GROUP", "device_ids": sorted(ids)})
        centre = {"storey_id": floor, **_centre(floor_rows)}
        centres.append(centre)
        if centre["excluded_device_ids"]:
            missing.append(
                {
                    "code": "PARTIAL_LOAD_CENTRE",
                    "storey_id": floor,
                    "device_ids": centre["excluded_device_ids"],
                }
            )
        by_storey.append(
            {
                "storey_id": floor,
                "name": snapshot.model.storey_names.get(floor, floor),
                "device_count": sum(row["kind"] in CONSUMING_KINDS for row in floor_rows),
                "device_ids": sorted(
                    row["load_id"] for row in floor_rows if row["kind"] in CONSUMING_KINDS
                ),
                "by_circuit_group": group_results,
                **_totals(floor_result),
            }
        )
    incomplete = any(value != "complete" for value in result["coverage"].values())
    output = {
        "design_context_id": design_context_id,
        "source_refs": dict(source_refs),
        "scope_type": scope_type,
        "scope_id": scope_id,
        "known_storey_ids": sorted(snapshot.model.floors),
        "status": "partial" if missing or incomplete or result["pending_requirements"] else "ok",
        **_totals(result),
        "by_storey": by_storey,
        "load_centres": centres,
        "load_breakdown": rows,
        "missing_data": missing,
        "warnings": warnings,
        "assumptions": result["assumptions"],
    }
    canonical = json.dumps(output, sort_keys=True, ensure_ascii=False, allow_nan=False)
    output["summary_id"] = "summary-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
    return output
