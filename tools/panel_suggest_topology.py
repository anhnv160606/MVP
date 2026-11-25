"""T2: đề xuất cây cấp điện theo quy tắc dự án và phạm vi tải đã xác nhận."""

import hashlib
import json
import math
from uuid import NAMESPACE_URL, uuid5

from database.project_store import require_id


def _identifier(plan_id, kind, key):
    return kind + "-" + str(uuid5(NAMESPACE_URL, f"{plan_id}:{kind}:{key}"))


def panel_suggest_topology(
    design_context_id: str,
    summary: dict,
    supply_entry: dict,
    policy: dict,
) -> dict:
    """Nhận summary T1, khảo sát và policy có nguồn; trả một topology hiện hành.

    policy.mode là centralized, per_storey hoặc service_groups.
    policy.service_groups gắn mỗi tầng vào một nhóm phục vụ khi mode=service_groups.
    Khảo sát cần confirmed=true, source, survey_revision và position_m toàn cục.
    Kết quả chưa xác nhận dung lượng cabinet hoặc vị trí lắp cụ thể.
    """
    require_id(design_context_id, "design_context_id")
    if summary.get("design_context_id") != design_context_id:
        raise ValueError("Summary không thuộc context đang thiết kế.")
    if summary.get("scope_type") != "building":
        raise ValueError("Topology cần summary của toàn công trình.")
    refs = dict(summary["source_refs"])
    for key in ("rule_id", "revision", "source"):
        require_id(policy.get(key), key)
    if policy["revision"] != refs["ruleset_revision"]:
        raise ValueError("Policy không thuộc revision quy tắc đã ghim.")
    mode = policy.get("mode")
    if mode not in {"centralized", "per_storey", "service_groups"}:
        raise ValueError("Chọn mode centralized, per_storey hoặc service_groups.")
    result = {
        "design_context_id": design_context_id,
        "summary_id": summary["summary_id"],
        "source_refs": refs,
        "status": "blocked",
        "topology_id": None,
        "panels": [],
        "feeders": [],
        "rule_ids": [policy["rule_id"]],
        "reasons": [],
        "missing_data": [],
        "warnings": list(summary.get("warnings", [])),
        "unassigned_device_ids": [],
        "pending_checks": ["cabinet_capacity", "panel_locations", "circuit_assignment"],
    }
    for key in ("survey_revision", "source", "source_id", "storey_id", "position_m"):
        if not supply_entry.get(key):
            result["missing_data"].append({"code": "MISSING_SURVEY_FIELD", "field": key})
    if supply_entry.get("confirmed") is not True:
        result["missing_data"].append({"code": "UNCONFIRMED_SUPPLY_ENTRY"})
    if supply_entry.get("storey_id") not in summary.get("known_storey_ids", []):
        result["missing_data"].append({"code": "UNKNOWN_SUPPLY_STOREY"})
    point = supply_entry.get("position_m", {})
    if not isinstance(point, dict) or any(
        type(point.get(axis)) not in (int, float) or not math.isfinite(point[axis])
        for axis in ("x", "y", "z")
    ):
        result["missing_data"].append({"code": "INVALID_SUPPLY_POSITION"})
    if supply_entry.get("coordinate_system") != "model_world" or supply_entry.get("unit") != "m":
        result["missing_data"].append({"code": "SUPPLY_COORDINATE_SYSTEM_NOT_CONFIRMED"})
    # Các tải chưa có tầng phải được giải quyết trước khi chia phạm vi phục vụ.
    for item in summary.get("missing_data", []):
        if item["code"] == "MISSING_STOREY":
            result["missing_data"].append(item)
    if result["missing_data"]:
        return result
    refs["survey_revision"] = supply_entry["survey_revision"]
    floors = {row["storey_id"]: row for row in summary["by_storey"] if row["device_count"] > 0}
    if not floors:
        result["missing_data"].append({"code": "NO_PLACED_SUPPLY_LOADS"})
        return result
    if summary["status"] == "partial":
        result["warnings"].append(
            {"code": "PARTIAL_LOAD_SUMMARY", "message": "Chưa thể xác nhận đủ tải thiết kế."}
        )
    main_floor = supply_entry["storey_id"]
    plan_id = refs["placement_plan_id"]
    main_id = _identifier(plan_id, "panel", "main")
    main = {
        "panel_id": main_id,
        "role": "main",
        "proposed_storey_id": main_floor,
        "service_group_ids": [],
        "served_storey_ids": [],
        "served_device_ids": [],
        "served_space_ids": [],
    }
    result["panels"].append(main)
    assignment = {}
    if mode == "centralized":
        assignment = {floor: "main" for floor in floors}
    elif mode == "per_storey":
        assignment = {
            floor: "main" if floor == main_floor else "floor:" + floor for floor in floors
        }
    else:
        groups = policy.get("service_groups", [])
        seen_groups = set()
        for group in groups:
            key = require_id(group.get("group_id"), "group_id")
            if key == "main" or key in seen_groups:
                raise ValueError("group_id bị trùng hoặc trùng mã dành riêng main.")
            seen_groups.add(key)
            if not group.get("storey_ids"):
                raise ValueError("Nhóm phục vụ phải có ít nhất một tầng.")
            for floor in group["storey_ids"]:
                if floor not in floors or floor in assignment:
                    raise ValueError("Tầng phục vụ không tồn tại hoặc được gán hai lần.")
                assignment[floor] = key
        if set(assignment) != set(floors):
            raise ValueError("Mọi tầng trong summary phải được gán vào đúng một nhóm.")
    groups = {}
    for floor, key in sorted(assignment.items()):
        groups.setdefault(key, []).append(floor)
    for key, served_floors in sorted(groups.items()):
        panel = main
        if key != "main":
            target_floor = served_floors[0]
            if mode == "service_groups":
                configured = next(
                    group for group in policy["service_groups"] if group["group_id"] == key
                )
                target_floor = configured.get("panel_storey_id", target_floor)
                if target_floor not in served_floors:
                    raise ValueError("Tầng đặt tủ phụ phải thuộc nhóm phục vụ đã xác nhận.")
            panel_id = _identifier(plan_id, "panel", key)
            panel = {
                "panel_id": panel_id,
                "role": "distribution",
                "proposed_storey_id": target_floor,
                "service_group_ids": [],
                "served_storey_ids": [],
                "served_device_ids": [],
                "served_space_ids": [],
            }
            result["panels"].append(panel)
            result["feeders"].append(
                {
                    "feeder_id": _identifier(plan_id, "feeder", key),
                    "upstream_id": main_id,
                    "downstream_panel_id": panel_id,
                    "served_storey_ids": served_floors,
                }
            )
        panel["service_group_ids"].append(key)
        panel["served_storey_ids"].extend(served_floors)
        panel["served_device_ids"] = sorted(
            {load_id for floor in served_floors for load_id in floors[floor]["device_ids"]}
        )
        device_ids = set(panel["served_device_ids"])
        panel["served_space_ids"] = sorted(
            {row["room_id"] for row in summary["load_breakdown"] if row["load_id"] in device_ids}
        )
    if supply_entry["source_id"] in {panel["panel_id"] for panel in result["panels"]}:
        raise ValueError("Mã nguồn cấp không được trùng mã tủ điện.")
    result["feeders"].insert(
        0,
        {
            "feeder_id": _identifier(plan_id, "feeder", "incoming"),
            "upstream_id": supply_entry["source_id"],
            "downstream_panel_id": main_id,
            "served_storey_ids": sorted(floors),
        },
    )
    result["status"] = "preliminary"
    result["reasons"] = [
        {
            "rule_id": policy["rule_id"],
            "mode": mode,
            "message": "Cấu trúc được sinh theo policy dự án; cần Dev B/C xác nhận cấu hình và vị trí.",
        }
    ]
    canonical = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False)
    result["topology_id"] = "topology-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
    return result
