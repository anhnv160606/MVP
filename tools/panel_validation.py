"""Deterministic validation with explicit, backend-owned approval stage policy."""

import math
from collections import Counter

from tools.panel_contracts import CalculationResult, PanelDesignContext, digest, schedule_data

REFERENCE_ERRORS = {
    "UNKNOWN_PANEL",
    "UNKNOWN_DEVICE",
    "UNKNOWN_CIRCUIT",
    "UNKNOWN_CATALOG",
    "UNKNOWN_SPACE",
    "UNKNOWN_STOREY",
    "UNKNOWN_PHASE_TARGET",
}


def panel_validate_schedule(
    proposed_schedule,
    *,
    context: PanelDesignContext,
    calculation=None,
    geometry=None,
    rule_results=None,
) -> dict:
    """Validate a complete panel schedule against a backend-pinned context.

    Input: PanelSchedule (or matching JSON); trusted context, calculation and
    geometry/rule evidence are injected by backend, never accepted from Agent.
    Checks cover supply-point assignment, tree topology, phase/voltage matching,
    catalog category, current ratings and total module capacity. Specialist
    checks must be supplied for the SAME schedule hash/context/source revision.
    Returns valid, complete, can_approve, errors, warnings, unperformed_checks,
    suggested_fixes and validated_schedule_hash. Missing inputs are explicit;
    valid=True alone never authorizes approval. No database or IFC writes.
    Malformed JSON/schema raises ValueError; design failures return issues.
    """
    data = schedule_data(proposed_schedule)
    schedule_hash = digest(data)
    errors, warnings, missing = [], [], []

    def issue(target, code, entity, message):
        item = {"code": code, "entity_id": entity, "message": message}
        item["issue_id"] = digest([code, entity, message])[:20]
        target.append(item)

    def error(code, entity, message):
        issue(errors, code, entity, message)

    def unchecked(code, entity, message):
        issue(missing, code, entity, message)

    def evidence(value, name):
        if value is None:
            unchecked(name, None, f"Backend chưa cung cấp {name}.")
            return None
        if not isinstance(value, dict) or any(
            value.get(key) != expected
            for key, expected in {
                "project_id": context.project_id,
                "design_context_id": context.design_context_id,
                "schedule_hash": schedule_hash,
                "source_refs": context.source_refs.model_dump(),
            }.items()
        ):
            error("STALE_EVIDENCE", name, "Kết quả không thuộc đúng project/context/hash/revision.")
            return None
        return value

    def check_result(results, key, entity):
        result = results.get(key) if isinstance(results, dict) else None
        if not isinstance(result, dict) or result.get("status") not in {"pass", "fail"}:
            unchecked(key, entity, "Chưa có kết quả kiểm tra được xác nhận.")
        elif result["status"] == "fail":
            error(key.upper(), entity, str(result.get("message", "Kiểm tra không đạt.")))

    panels = {x["panel_id"]: x for x in data["panels"]}
    circuits = {x["circuit_id"]: x for x in data["final_circuits"]}
    feeders = {x["feeder_id"]: x for x in data["feeders"]}
    phase_map = {}
    for assignment in data["phase_assignments"]:
        target = assignment["circuit_id"] or assignment["feeder_id"]
        collection = circuits if assignment["circuit_id"] else feeders
        if target not in collection:
            error("UNKNOWN_PHASE_TARGET", target, "Phân pha tham chiếu sai mạch/feeder.")
        phase_map[target] = assignment["phases"]
    # Hash above is the external v2 contract; phases below are a local view only.
    for target, entity in {**circuits, **feeders}.items():
        entity["phases"] = phase_map.get(target, [])
        if not entity["phases"]:
            unchecked("PHASE_ASSIGNMENT", target, "Chưa phân pha cho mạch/feeder.")
    if not context.supply_phases:
        unchecked("SUPPLY_PHASES", None, "Nguồn cấp chưa được xác nhận số pha.")
    devices = {x.device_id: x for x in context.devices}
    catalog = {x.catalog_id: x for x in context.catalog}
    components = {key: [] for key in panels}
    if context.status in {"blocked", "no_match"}:
        error("CONTEXT_BLOCKED", None, "Context chưa đủ điều kiện thiết kế.")

    def component(catalog_id, categories, entity, panel_id):
        if catalog_id is None:
            unchecked("MISSING_SELECTION", entity, "Chưa chọn thiết bị catalog.")
            return None
        item = catalog.get(catalog_id)
        if item is None:
            error("UNKNOWN_CATALOG", entity, f"Catalog không thuộc context: {catalog_id}")
            return None
        if item.category not in categories:
            error("CATALOG_CATEGORY", entity, f"Loại catalog {item.category} không phù hợp.")
        if item.verification_status != "verified":
            unchecked(
                "CATALOG_UNVERIFIED",
                catalog_id,
                "Thông số catalog còn là tham khảo/chưa kiểm chứng.",
            )
        if item.missing_data:
            unchecked("CATALOG_MISSING_DATA", catalog_id, "Catalog còn dữ liệu cần bổ sung.")
        if panel_id in components and item.category != "cabinet":
            components[panel_id].append(item)
        return item

    main = [p["panel_id"] for p in panels.values() if p["role"] == "main"]
    if len(main) != 1:
        error("SOURCE_COUNT", None, "MVP yêu cầu đúng một tủ chính.")
    adjacency = {key: [] for key in panels}
    incoming = Counter()
    for feeder in data["feeders"]:
        fid = feeder["feeder_id"]
        up, down = feeder["upstream_panel_id"], feeder["downstream_panel_id"]
        if up not in panels or down not in panels:
            error("UNKNOWN_PANEL", fid, "Feeder tham chiếu tủ không tồn tại.")
            continue
        adjacency[up].append(down)
        incoming[down] += 1
        if up == down:
            error("SELF_FEEDER", fid, "Feeder không được tự nối.")
    # Iterative graph walk handles cycles without recursive depth failures.
    degree = {key: incoming[key] for key in panels}
    queue = [key for key in panels if degree[key] == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for child in adjacency[current]:
            degree[child] -= 1
            if degree[child] == 0:
                queue.append(child)
    if visited != len(panels):
        error("FEEDER_CYCLE", None, "Topology có chu trình.")
    for pid, panel in panels.items():
        if incoming[pid] != (0 if panel["role"] == "main" else 1):
            error(
                "PANEL_SUPPLY", pid, "Tủ chính không có feeder vào; tủ con cần đúng một feeder vào."
            )
    panel_phases = {pid: set(context.supply_phases) for pid in panels}
    for feeder in data["feeders"]:
        panel_phases[feeder["downstream_panel_id"]] = set(feeder["phases"])

    assignments = Counter()
    load_assignments = Counter()
    selected = {}
    for circuit in circuits.values():
        cid, pid = circuit["circuit_id"], circuit["panel_id"]
        if pid not in panels:
            error("UNKNOWN_PANEL", cid, "Mạch tham chiếu tủ không tồn tại.")
        if not circuit["device_ids"]:
            error("EMPTY_CIRCUIT", cid, "Mạch cuối chưa có điểm tải.")
        for did in circuit["device_ids"]:
            device = devices.get(did)
            if device is None:
                error("UNKNOWN_DEVICE", cid, f"Thiết bị không thuộc context: {did}")
                continue
            assignments[did] += 1
            load_assignments[device.supply_point_id or did] += 1
            if not device.requires_supply:
                error("NOT_A_LOAD", did, "Đối tượng này không phải điểm tải cần cấp điện.")
            if device.dedicated_circuit and len(circuit["device_ids"]) != 1:
                error("DEDICATED_CIRCUIT", did, "Thiết bị yêu cầu mạch riêng.")
            if device.phases is None:
                unchecked("DEVICE_PHASES", did, "Chưa xác định số pha của thiết bị.")
            elif circuit["phases"] and device.phases != len(circuit["phases"]):
                error("DEVICE_PHASES", did, "Số pha thiết bị và mạch không khớp.")
            if device.voltage_V is None or circuit["voltage_V"] is None:
                unchecked("DEVICE_VOLTAGE", did, "Chưa đủ dữ liệu điện áp.")
            elif not math.isclose(device.voltage_V, circuit["voltage_V"], rel_tol=1e-6):
                error("DEVICE_VOLTAGE", did, "Điện áp thiết bị và mạch không khớp.")
    required_points = {
        d.supply_point_id or d.device_id for d in devices.values() if d.requires_supply
    }
    for point in sorted(required_points):
        if load_assignments[point] != 1:
            error("SUPPLY_ASSIGNMENT", point, "Mỗi điểm tải cần được phân vào đúng một mạch.")
    for did, count in assignments.items():
        if count > 1:
            error("DUPLICATE_DEVICE", did, "Thiết bị bị phân mạch nhiều lần.")
    if (
        not context.inventory_complete
        or context.next_cursor is not None
        or context.unresolved_requirements
        or context.unmapped_devices
        or context.missing_data
    ):
        unchecked(
            "DEVICE_INVENTORY", None, "Danh sách thiết bị đã duyệt chưa được xác nhận đầy đủ."
        )

    for circuit in [*data["final_circuits"], *data["feeders"]]:
        cid = circuit.get("circuit_id", circuit.get("feeder_id"))
        pid = circuit.get("panel_id", circuit.get("upstream_panel_id"))
        phases = circuit["phases"]
        if (
            phases
            and context.supply_phases
            and (
                len(phases) not in {1, 3}
                or len(set(phases)) != len(phases)
                or not set(phases) <= set(context.supply_phases)
            )
        ):
            error("INVALID_PHASES", cid, "Pha phải là một pha khả dụng hoặc đủ L1/L2/L3.")
        if pid in panel_phases and panel_phases[pid] and not set(phases) <= panel_phases[pid]:
            error("FEEDER_PHASES", cid, "Mạch dùng pha không được feeder đầu vào cấp.")
        item = component(circuit["breaker_catalog_id"], {"mcb", "mccb", "rcbo"}, cid, pid)
        selected[cid] = item
        if item:
            if item.poles is None:
                unchecked("BREAKER_POLES", cid, "Thiếu số cực thiết bị bảo vệ.")
            elif phases and item.poles not in ({1, 2} if len(phases) == 1 else {3, 4}):
                error("BREAKER_POLES", cid, "Số cực không tương thích số pha mạch.")
            voltage = circuit.get("voltage_V")
            if voltage is None or item.rated_voltage_V is None:
                unchecked("BREAKER_VOLTAGE", cid, "Thiếu điện áp định mức/mạch.")
            elif item.rated_voltage_V < voltage:
                error("BREAKER_VOLTAGE", cid, "Điện áp định mức thiết bị thấp hơn điện áp mạch.")

    protected = Counter()
    for group in data["protection_groups"]:
        gid, pid = group["protection_group_id"], group["panel_id"]
        if pid not in panels:
            error("UNKNOWN_PANEL", gid, "Nhóm bảo vệ tham chiếu tủ không tồn tại.")
        component(group["catalog_id"], {"rccb"}, gid, pid)
        for cid in group["circuit_ids"]:
            protected[cid] += 1
            if cid not in circuits:
                error("UNKNOWN_CIRCUIT", gid, f"Mạch không tồn tại: {cid}")
            elif circuits[cid]["panel_id"] != pid:
                error("PROTECTION_PANEL", gid, "Nhóm RCCB và mạch phải cùng tủ.")
            elif selected.get(cid) and selected[cid].category == "rcbo":
                error(
                    "PROTECTION_SCHEME",
                    cid,
                    "MVP chọn RCCB nhóm hoặc RCBO riêng; chưa hỗ trợ phối hợp cả hai.",
                )
    for cid, count in protected.items():
        if count > 1:
            error("DUPLICATE_PROTECTION", cid, "Một mạch thuộc nhiều nhóm RCCB.")

    cabinets, mains = {}, {}
    for pid, panel in panels.items():
        cabinets[pid] = component(panel["cabinet_catalog_id"], {"cabinet"}, pid, pid)
        mains[pid] = component(panel["main_breaker_catalog_id"], {"mcb", "mccb", "rcbo"}, pid, pid)
        main_device, cabinet_device = mains[pid], cabinets[pid]
        if main_device:
            if main_device.poles is None:
                unchecked("MAIN_POLES", pid, "Thiếu số cực bảo vệ chính.")
            elif panel_phases[pid] and main_device.poles not in (
                {1, 2} if len(panel_phases[pid]) == 1 else {3, 4}
            ):
                error("MAIN_POLES", pid, "Số cực bảo vệ chính không khớp nguồn cấp tủ.")
        if (
            main_device
            and cabinet_device
            and main_device.rated_current_A is not None
            and cabinet_device.rated_current_A is not None
            and main_device.rated_current_A > cabinet_device.rated_current_A
        ):
            error("MAIN_CABINET_RATING", pid, "Dòng định mức bảo vệ chính vượt định mức tủ.")
        for accessory in panel["accessory_catalog_ids"]:
            component(accessory, {"accessory"}, pid, pid)
        if panel["storey_id"] is None:
            unchecked("PANEL_STOREY", pid, "Chưa chọn tầng đặt tủ.")
        elif panel["storey_id"] not in context.storey_ids:
            error("UNKNOWN_STOREY", pid, "Tầng không thuộc context.")
        location = panel["location"]
        if location is None:
            unchecked("PANEL_LOCATION", pid, "Chưa chọn vị trí tủ.")
        elif location["space_id"] not in context.space_ids:
            error("UNKNOWN_SPACE", pid, "Phòng không thuộc context.")
        elif location["geometry_revision"] != context.source_refs.model_revision:
            error("STALE_GEOMETRY", pid, "Vị trí lấy từ phiên bản hình học đã cũ.")
        cabinet = cabinets[pid]
        parts = components[pid]
        if (
            cabinet
            and cabinet.available_modules is not None
            and all(x.module_count is not None for x in parts)
        ):
            used = sum(x.module_count for x in parts)
            if used > cabinet.available_modules:
                error("MODULE_CAPACITY", pid, "Thiết bị vượt dung lượng mô đun tủ.")
            if context.required_spare_percent is None:
                unchecked("MODULE_RESERVE", pid, "Chưa xác nhận quy tắc dự phòng tủ.")
            elif used > cabinet.available_modules * (1 - context.required_spare_percent / 100):
                error("MODULE_RESERVE", pid, "Không đủ chỗ trống theo phần trăm dung lượng tủ.")
        else:
            unchecked("MODULE_CAPACITY", pid, "Thiếu dữ liệu module của tủ hoặc thiết bị.")

    calc = evidence(calculation, "calculation")
    if calc:
        calc = CalculationResult.model_validate(calc).model_dump()
        if calc["status"] in {"blocked", "no_match"}:
            error("CALCULATION_BLOCKED", None, "Backend không tính được phương án này.")
        if calc["coverage"].get("complete") is not True or calc["missing_data"]:
            unchecked("LOAD_COVERAGE", None, "Kết quả tính tải chưa đầy đủ.")
        for group, entities, key in (
            ("circuit_results", data["final_circuits"], "circuit_id"),
            ("feeder_results", data["feeders"], "feeder_id"),
            ("panel_results", data["panels"], "panel_id"),
        ):
            results = {item[key]: item for item in calc[group]}
            expected_ids = {entity[key] for entity in entities}
            if set(results) - expected_ids:
                error("CALCULATION_TARGETS", group, "Kết quả tính chứa entity ngoài schedule.")
            for entity in entities:
                eid = entity[key]
                result = results.get(eid, {}) if isinstance(results, dict) else {}
                currents = (
                    result.get("design_current_A_by_phase", {}) if isinstance(result, dict) else {}
                )
                phases = entity.get("phases", sorted(panel_phases.get(eid, set())))
                if (
                    not phases
                    or not isinstance(currents, dict)
                    or not all(
                        type(currents.get(p)) in (int, float)
                        and math.isfinite(currents[p])
                        and currents[p] >= 0
                        for p in phases
                    )
                ):
                    unchecked("DESIGN_CURRENT", eid, "Thiếu dòng tính toán theo pha.")
                    continue
                design_current = max(currents[p] for p in phases)
                targets = [selected.get(eid)] if key != "panel_id" else [mains[eid], cabinets[eid]]
                for target in targets:
                    if target is None or target.rated_current_A is None:
                        unchecked("RATED_CURRENT", eid, "Thiếu dòng định mức catalog.")
                    elif target.rated_current_A < design_current:
                        error(
                            "CURRENT_CAPACITY",
                            eid,
                            "Dòng tính toán vượt dòng định mức thiết bị/tủ.",
                        )
        for check in (
            "cable_protection",
            "breaking_capacity",
            "load_characteristics",
            "residual_coordination",
            "phase_balance",
            "voltage_drop",
        ):
            check_result(calc.get("engineering_checks"), check, None)

    geo = evidence(geometry, "geometry")
    if geo:
        for pid in panels:
            entries = geo.get("panels", {})
            results = entries.get(pid, {}) if isinstance(entries, dict) else {}
            for check in (
                "room_membership",
                "host_surface",
                "orientation",
                "collision",
                "clearance",
                "component_fit",
            ):
                check_result(results, check, pid)
    rules = evidence(rule_results, "project_rules")
    if rules:
        check_result(rules.get("checks"), "project_rules", None)
        # Advisory rules can be reported separately by the trusted rule engine.
        advisories = rules.get("warnings", [])
        if not isinstance(advisories, list):
            unchecked("RULE_WARNINGS", None, "Rule engine trả warnings sai cấu trúc.")
            advisories = []
        for advisory in advisories:
            if isinstance(advisory, dict) and isinstance(advisory.get("message"), str):
                issue(
                    warnings,
                    str(advisory.get("code", "PROJECT_ADVISORY")),
                    advisory.get("entity_id"),
                    advisory["message"],
                )
    # Stable issue IDs permit explicit review of warnings in the future approval gate.
    for target in (errors, warnings, missing):
        target[:] = list({item["issue_id"]: item for item in target}.values())
    valid, complete = not errors, not missing
    policy = context.approval_policy
    deferred = set(policy.defer_to_routing) if policy.stage == "panel_design" else set()
    for item in missing:
        item["blocking"] = item["code"] not in deferred
        item["required_at"] = "panel_design" if item["blocking"] else "routing"
    return {
        "valid": valid,
        "complete": complete,
        "can_approve": valid and not any(item["blocking"] for item in missing),
        "approval_scope": policy.stage,
        "deferred_checks": [item for item in missing if not item["blocking"]],
        "errors": errors,
        "warnings": warnings,
        "unperformed_checks": missing,
        "suggested_fixes": [
            {
                "issue_id": i["issue_id"],
                "entity_id": i["entity_id"],
                "action": "review_input",
                "message": i["message"],
            }
            for i in errors + missing
        ],
        "validated_schedule_hash": schedule_hash,
    }
