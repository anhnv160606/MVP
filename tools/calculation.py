"""Preliminary connected and estimated load summaries for Phase 2."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def calc_preliminary_load(scope_type: str, scope_id: str, plan_id: str | None = None,
                         placements: list[dict[str, Any]] | None = None,
                         declared_loads: list[dict[str, Any]] | None = None,
                         calculation_rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate explicit device ratings and declared special loads.

    A declared load is matched by ``source_id``/``device_id`` to avoid adding
    the same physical load twice. Unknown power and demand factors stay unknown.
    """
    if scope_type not in {"space", "storey", "zone", "project"}:
        raise ValueError("scope_type must be space, storey, zone, or project")
    rules = calculation_rules or {}
    selected = list(placements or [])
    if scope_type == "space":
        selected = [p for p in selected if str(p.get("space_id")) == str(scope_id)]
    elif scope_type == "storey":
        selected = [p for p in selected if str(p.get("storey_id")) == str(scope_id)]
    elif scope_type == "zone":
        selected = [p for p in selected if str(p.get("zone_id")) == str(scope_id)]
    declared = list(declared_loads or [])
    if scope_type == "space":
        declared = [x for x in declared if str(x.get("space_id")) == str(scope_id)]
    elif scope_type == "storey":
        declared = [x for x in declared if str(x.get("storey_id")) == str(scope_id)]
    elif scope_type == "zone":
        declared = [x for x in declared if str(x.get("zone_id")) == str(scope_id)]

    physical_ids = {str(p.get("element_id") or p.get("proposed_id")) for p in selected if p.get("element_id") or p.get("proposed_id")}
    deduped, seen_declared = [], set()
    for load in declared:
        source_id = load.get("device_id") or load.get("source_id")
        key = str(source_id) if source_id else None
        if key and (key in physical_ids or key in seen_declared):
            continue
        if key:
            seen_declared.add(key)
        deduped.append(load)

    breakdown: dict[str, dict[str, Any]] = defaultdict(lambda: {"connected_load_W": 0.0, "estimated_demand_W": 0.0, "count": 0, "unknown_power_count": 0})
    assumptions, warnings = [], []
    if selected and any(not (load.get("device_id") or load.get("source_id")) for load in deduped):
        warnings.append("Một số phụ tải khai báo thiếu mã thiết bị nguồn; không thể loại trừ cộng trùng với thiết bị đã bố trí.")
    if not selected and not deduped:
        warnings.append("Không có thiết bị hoặc phụ tải khai báo để tính; không coi tổng bằng 0 là phụ tải thực tế.")
    connected, estimated = 0.0, 0.0
    connected_known = estimated_known = bool(selected or deduped)
    entries = [(p, "placement") for p in selected] + [(x, "declared") for x in deduped]
    for item, origin in entries:
        category = item.get("load_class_id") or item.get("load_class") or item.get("device_type") or item.get("name") or "unclassified"
        power = item.get("rated_power_W", item.get("power_W", item.get("declared_power_W")))
        factor = item.get("demand_factor")
        if factor is None:
            factor = rules.get("demand_factors", {}).get(category)
        breakdown[category]["count"] += 1
        if power is None:
            connected_known = estimated_known = False
            breakdown[category]["unknown_power_count"] += 1
            warnings.append(f"{category}: thiếu công suất; không cộng tải giả định.")
            continue
        power = float(power)
        connected += power
        breakdown[category]["connected_load_W"] += power
        if factor is None:
            estimated_known = False
            warnings.append(f"{category}: thiếu hệ số nhu cầu đã được xác nhận; phụ tải tính toán chưa đủ dữ liệu.")
        else:
            factor = float(factor)
            if not 0 < factor <= 1:
                raise ValueError(f"Demand factor for {category} must be in (0, 1].")
            contribution = power * factor
            estimated += contribution
            breakdown[category]["estimated_demand_W"] += contribution
            assumptions.append({"load_class": category, "origin": origin, "demand_factor": factor})
    return {"scope_type": scope_type, "scope_id": scope_id, "plan_id": plan_id,
            "connected_load_W": round(connected, 3) if connected_known else None,
            "estimated_demand_W": round(estimated, 3) if estimated_known else None,
            "load_breakdown": dict(breakdown), "assumptions": assumptions, "warnings": list(dict.fromkeys(warnings))}
