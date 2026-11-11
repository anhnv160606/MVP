"""Deterministic, explainable placement planning and validation."""
from __future__ import annotations

from math import hypot
from typing import Any
from uuid import uuid4


def _point(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, dict):
        try:
            return float(value["x"]), float(value["y"]), float(value.get("z", 0))
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1]), float(value[2] if len(value) > 2 else 0)
        except (TypeError, ValueError):
            return None
    return None


def _segment(wall: dict[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    start, end = _point(wall.get("start")), _point(wall.get("end"))
    if start and end:
        return start, end
    geometry = wall.get("geometry") or {}
    bbox = geometry.get("bbox", wall.get("bbox")) if isinstance(geometry, dict) else wall.get("bbox")
    if isinstance(bbox, dict):
        try:
            if "min" in bbox and "max" in bbox:
                a, b = _point(bbox["min"]), _point(bbox["max"])
                if a and b:
                    # Longest horizontal bbox axis is the wall's usable segment.
                    return ((a[0], (a[1] + b[1]) / 2, a[2]), (b[0], (a[1] + b[1]) / 2, a[2])) if abs(b[0]-a[0]) >= abs(b[1]-a[1]) else (((a[0]+b[0])/2, a[1], a[2]), ((a[0]+b[0])/2, b[1], a[2]))
        except (TypeError, ValueError):
            return None
    return None


def _ceiling_bbox(ceiling: dict[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    geometry = ceiling.get("geometry") or ceiling
    bbox = geometry.get("bbox", geometry) if isinstance(geometry, dict) else {}
    if isinstance(bbox, dict) and "min" in bbox and "max" in bbox:
        a, b = _point(bbox["min"]), _point(bbox["max"])
        if a and b:
            return a, b
    return None


def _rule_quantity(rule: dict[str, Any]) -> int | None:
    quantity = rule.get("quantity")
    if quantity is None:
        quantity_rule = rule.get("quantity_rule")
        if isinstance(quantity_rule, dict):
            quantity = quantity_rule.get("count", quantity_rule.get("min"))
    if quantity is None:
        quantity = rule.get("min_qty")
    try:
        number = int(quantity)
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def _numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def engine_plan_placements(space_ids: list[str], placement_context: dict[str, dict[str, Any]],
                           rules: list[dict[str, Any]], device_specs: list[dict[str, Any]],
                           existing_devices: list[dict[str, Any]] | None = None,
                           questionnaire_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generate candidates only where the supplied geometry and rules support them.

    The planner does not invent a device count. Rules may provide ``quantity``,
    ``min_qty`` or a structured ``quantity_rule`` with ``count``/``min``.
    """
    existing = existing_devices or []
    proposals, rejected, warnings = [], [], []
    seen: list[tuple[dict[str, Any], tuple[float, float, float]]] = []
    for device in existing:
        p = _point(device.get("position"))
        if p:
            seen.append((device, p))
    qcontext = questionnaire_context or {}
    for sid in space_ids:
        ctx = placement_context.get(str(sid))
        if not ctx:
            warnings.append(f"{sid}: thiếu placement context.")
            continue
        if not ctx.get("space_type"):
            warnings.append(f"{sid}: chưa xác định được loại phòng; không áp dụng rule theo tên suy đoán.")
            continue
        if ctx.get("missing_data"):
            warnings.append(f"{sid}: IFC thiếu dữ liệu {', '.join(ctx['missing_data'])}.")
        room_needs = qcontext.get("per_space_requirements", {}).get(str(sid), {})
        for spec in device_specs:
            dtype = spec.get("device_type") or spec.get("category")
            if not dtype:
                warnings.append(f"{sid}: mẫu thiết bị thiếu device_type.")
                continue
            matching = [r for r in rules if (not r.get("device_type") or r.get("device_type") == dtype)
                        and (not r.get("space_type") or str(r.get("space_type")).casefold() == str(ctx.get("space_type", "")).casefold())]
            rule = next(iter(matching), None)
            if rule is None:
                rejected.append({"space_id": sid, "device_type": dtype, "reason": "no_applicable_rule"})
                continue
            needed = _rule_quantity(rule)
            if needed is None:
                declared = room_needs.get(dtype) if isinstance(room_needs, dict) else None
                if isinstance(declared, dict):
                    needed = _rule_quantity(declared)
                if needed is None:
                    rejected.append({"space_id": sid, "device_type": dtype, "reason": "quantity_not_defined", "rule_id": rule.get("rule_id")})
                    continue
            requested_host = str(rule.get("host_type") or spec.get("host_type") or "wall").casefold()
            hosts = ctx.get("walls", []) if requested_host == "wall" else []
            if requested_host == "ceiling":
                ceiling = ctx.get("ceiling")
                hosts = [ceiling] if ceiling else []
            if not hosts:
                rejected.append({"space_id": sid, "device_type": dtype, "reason": "no_usable_host_surface", "rule_id": rule.get("rule_id")})
                continue
            host_type = requested_host
            if host_type == "ceiling":
                bounds = _ceiling_bbox(hosts[0]) if hosts and isinstance(hosts[0], dict) else None
                if not bounds:
                    rejected.append({"space_id": sid, "device_type": dtype, "reason": "ceiling_geometry_missing", "rule_id": rule.get("rule_id")})
                    continue
                lo, hi = bounds
                cols = max(1, int(needed ** 0.5 + 0.999))
                rows = max(1, (needed + cols - 1) // cols)
                z = hi[2]
                for n in range(needed):
                    col, row = n % cols, n // cols
                    position = {"x": round(lo[0] + (col + 1) * (hi[0]-lo[0])/(cols+1), 4),
                                "y": round(lo[1] + (row + 1) * (hi[1]-lo[1])/(rows+1), 4), "z": round(z, 4)}
                    proposals.append({"proposed_id": str(uuid4()), "device_type": dtype, "space_id": str(sid),
                                      "storey_id": ctx.get("storey_id"),
                                      "host_id": hosts[0].get("element_id") or hosts[0].get("id"), "host_type": "ceiling",
                                      "ifc_class": spec.get("ifc_class"), "position": position, "orientation": spec.get("orientation"),
                                      "module_id": spec.get("module_id"), "geometry_ref": spec.get("geometry_ref"),
                                      "rated_power_W": spec.get("rated_power_W"), "load_class_id": spec.get("load_class_id"),
                                      "demand_factor": spec.get("demand_factor"),
                                      "load_type": spec.get("load_type"), "connection_specs": spec.get("connection_specs"),
                                      "voltage_v": spec.get("voltage_v"), "phases": spec.get("phases"),
                                      "terminal_class_id": spec.get("terminal_class_id"), "rule_ids": [rule.get("rule_id")] if rule.get("rule_id") else [],
                                      "source": rule.get("source")})
                continue
            available = []
            for index, host in enumerate(hosts):
                if not isinstance(host, dict):
                    continue
                seg = _segment(host)
                if seg:
                    a, b = seg
                    length = hypot(b[0]-a[0], b[1]-a[1])
                    if length > float(rule.get("end_clearance_m", 0.15)) * 2:
                        available.append((host, a, b, length, index))
            if not available:
                rejected.append({"space_id": sid, "device_type": dtype, "reason": "wall_segment_geometry_missing", "rule_id": rule.get("rule_id")})
                continue
            total_length = sum(item[3] for item in available)
            for n in range(needed):
                target = (n + 1) * total_length / (needed + 1)
                chosen = available[-1]
                for item in available:
                    if target <= item[3]:
                        chosen = item
                        break
                    target -= item[3]
                host, a, b, length, _ = chosen
                t = max(0.05, min(0.95, target / length))
                x, y = a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t
                height = spec.get("mounting_height_m", rule.get("mounting_height_m"))
                if height is None:
                    warnings.append(f"{sid}/{dtype}: chưa có cao độ lắp đặt; không thể suy ra z chính xác.")
                    continue
                base_z = ctx.get("floor_elevation_m")
                room_bbox = (ctx.get("geometry") or {}).get("bbox", {}) if isinstance(ctx.get("geometry"), dict) else {}
                if base_z is None and isinstance(room_bbox, dict) and room_bbox.get("min") is not None:
                    floor_point = _point(room_bbox["min"])
                    base_z = floor_point[2] if floor_point else None
                if base_z is None:
                    warnings.append(f"{sid}/{dtype}: thiếu cao độ sàn; không thể quy đổi cao độ lắp đặt sang hệ tọa độ IFC.")
                    continue
                try:
                    absolute_z = float(base_z) + float(height)
                except (TypeError, ValueError):
                    warnings.append(f"{sid}/{dtype}: cao độ '{height}' không phải số mét; chưa thể đặt.")
                    continue
                position = {"x": round(x, 4), "y": round(y, 4), "z": round(absolute_z, 4)}
                duplicate = next((old for old, old_point in seen if str(old.get("space_id")) == str(sid)
                                  and old.get("device_type") == dtype and hypot(x-old_point[0], y-old_point[1]) < 0.05), None)
                if duplicate:
                    rejected.append({"space_id": sid, "device_type": dtype, "reason": "existing_or_duplicate_position",
                                     "element_id": duplicate.get("element_id"), "proposed_id": duplicate.get("proposed_id")})
                    continue
                proposal = {"proposed_id": str(uuid4()), "device_type": dtype, "space_id": str(sid),
                            "storey_id": ctx.get("storey_id"),
                            "host_id": host.get("element_id") or host.get("id"), "host_type": rule.get("host_type", "wall"),
                            "ifc_class": spec.get("ifc_class"), "position": position, "orientation": spec.get("orientation"),
                            "module_id": spec.get("module_id"), "geometry_ref": spec.get("geometry_ref"),
                            "rated_power_W": spec.get("rated_power_W"), "load_class_id": spec.get("load_class_id"),
                            "demand_factor": spec.get("demand_factor"),
                            "load_type": spec.get("load_type"), "connection_specs": spec.get("connection_specs"),
                            "voltage_v": spec.get("voltage_v"), "phases": spec.get("phases"),
                            "terminal_class_id": spec.get("terminal_class_id"), "rule_ids": [rule.get("rule_id")] if rule.get("rule_id") else [],
                            "source": rule.get("source")}
                proposals.append(proposal)
                seen.append((proposal, (x, y, absolute_z)))
    if not proposals:
        warnings.append("Không sinh được đề xuất; cần bổ sung quy tắc số lượng, bề mặt hoặc hình học/cao độ còn thiếu.")
    return {"proposals": proposals, "rejected_candidates": rejected, "warnings": warnings}


def engine_validate_placements(proposals: list[dict[str, Any]], placement_context: dict[str, dict[str, Any]],
                               rules: list[dict[str, Any]], tolerance_m: float = 0.05,
                               existing_devices: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    errors, warnings, fixes = [], [], []
    seen: list[tuple[dict[str, Any], tuple[float, float, float]]] = []
    proposed_ids: set[str] = set()
    for device in existing_devices or []:
        p = _point(device.get("position"))
        if p:
            props = device.get("properties") or {}
            seen.append(({**device, "proposed_id": props.get("ProposedId")}, p))
    for index, proposal in enumerate(proposals):
        proposed_id = proposal.get("proposed_id")
        if proposed_id and str(proposed_id) in proposed_ids:
            errors.append({"code": "duplicate_proposed_id", "index": index, "message": "proposed_id bị lặp trong cùng phương án."})
        if proposed_id:
            proposed_ids.add(str(proposed_id))
        sid, dtype = str(proposal.get("space_id", "")), proposal.get("device_type")
        ctx = placement_context.get(sid)
        p = _point(proposal.get("position"))
        if not ctx:
            errors.append({"code": "space_context_missing", "index": index, "message": f"Không có context cho phòng {sid}."})
            continue
        if p is None:
            errors.append({"code": "invalid_position", "index": index, "message": "Thiếu tọa độ x/y/z hợp lệ."})
            continue
        geom = ctx.get("geometry") or {}
        bbox = geom.get("bbox", geom) if isinstance(geom, dict) else {}
        if isinstance(bbox, dict) and "min" in bbox and "max" in bbox:
            lo, hi = _point(bbox["min"]), _point(bbox["max"])
            if lo and hi and not all(lo[d]-tolerance_m <= p[d] <= hi[d]+tolerance_m for d in range(3)):
                errors.append({"code": "outside_space", "index": index, "message": "Thiết bị nằm ngoài bounding box không gian."})
        elif not geom:
            warnings.append({"code": "space_geometry_missing", "index": index, "message": "Không thể xác nhận điểm nằm trong phòng vì thiếu geometry."})
        polygon = geom.get("polygon_2d") if isinstance(geom, dict) else None
        if polygon and p:
            inside, j = False, len(polygon)-1
            for i, vertex in enumerate(polygon):
                vp, vj = _point(vertex), _point(polygon[j])
                if vp and vj and ((vp[1] > p[1]) != (vj[1] > p[1])) and p[0] < (vj[0]-vp[0])*(p[1]-vp[1])/(vj[1]-vp[1]) + vp[0]:
                    inside = not inside
                j = i
            if not inside:
                errors.append({"code": "outside_space_polygon", "index": index, "message": "Thiết bị nằm ngoài polygon của phòng."})
        if not proposal.get("host_id"):
            warnings.append({"code": "host_unassigned", "index": index, "message": "Chưa liên kết thiết bị với tường/trần cụ thể."})
        elif proposal.get("host_type") == "wall":
            host = next((w for w in ctx.get("walls", []) if isinstance(w, dict) and str(w.get("element_id") or w.get("id")) == str(proposal["host_id"])), None)
            seg = _segment(host) if host else None
            if seg:
                a, b = seg
                dx, dy = b[0]-a[0], b[1]-a[1]
                length2 = dx*dx + dy*dy
                t = max(0.0, min(1.0, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / length2)) if length2 else 0.0
                distance = hypot(p[0]-(a[0]+t*dx), p[1]-(a[1]+t*dy))
                if distance > tolerance_m:
                    errors.append({"code": "not_on_host_surface", "index": index, "message": "Tọa độ không nằm trên đoạn tường được gán."})
            elif host is None:
                errors.append({"code": "host_not_in_space", "index": index, "message": "host_id không thuộc tường của phòng."})
        for kind in ("doors", "windows", "obstacles"):
            for obstacle in ctx.get(kind, []) or []:
                geometry = obstacle.get("geometry") if isinstance(obstacle, dict) else None
                bbox = geometry.get("bbox", geometry) if isinstance(geometry, dict) else {}
                blocked = False
                if isinstance(bbox, dict) and "min" in bbox and "max" in bbox:
                    lo, hi = _point(bbox["min"]), _point(bbox["max"])
                    if lo and hi:
                        clearance = float(obstacle.get("clearance_m", 0.3))
                        blocked = all(lo[d]-clearance <= p[d] <= hi[d]+clearance for d in range(3))
                op = _point(obstacle.get("position") if isinstance(obstacle, dict) else None)
                if (blocked or (op and hypot(p[0]-op[0], p[1]-op[1]) < float(obstacle.get("clearance_m", 0.3)))):
                    errors.append({"code": f"near_{kind[:-1]}", "index": index, "element_id": obstacle.get("element_id"),
                                   "message": f"Vị trí quá gần {kind}; cần hình học/khoảng hở chính xác để kiểm tra va chạm."})
                    fixes.append({"index": index, "action": "move_to_clear_wall_segment"})
        for previous, pp in seen:
            if proposal.get("proposed_id") and proposal.get("proposed_id") == previous.get("proposed_id"):
                continue
            if previous.get("space_id") == sid and previous.get("device_type") == dtype and hypot(p[0]-pp[0], p[1]-pp[1]) < tolerance_m:
                errors.append({"code": "duplicate_device_position", "index": index, "other_proposed_id": previous.get("proposed_id"), "message": "Thiết bị trùng vị trí."})
                fixes.append({"index": index, "action": "choose_another_candidate"})
        seen.append((proposal, p))
        if proposal.get("host_type") and proposal["host_type"] not in {"wall", "ceiling"}:
            errors.append({"code": "invalid_host_type", "index": index, "message": "Bề mặt lắp đặt không hợp lệ."})
        applicable = [r for r in rules if r.get("rule_id") in (proposal.get("rule_ids") or [])]
        if proposal.get("rule_ids") and not applicable:
            warnings.append({"code": "rule_not_found", "index": index, "message": "Rule ID trong đề xuất không tồn tại trong rules đầu vào."})
        for rule in applicable:
            expected_host = rule.get("host_type")
            if expected_host and proposal.get("host_type") and expected_host != proposal["host_type"]:
                errors.append({"code": "host_type_rule_mismatch", "index": index, "rule_id": rule.get("rule_id"), "message": "Bề mặt lắp đặt không khớp rule."})
            height = _numeric(rule.get("mounting_height_m"))
            if height is None and rule.get("mounting_height"):
                height = _numeric(rule.get("mounting_height"))
            if height is not None:
                base = ctx.get("floor_elevation_m")
                if base is None and isinstance(geom, dict):
                    room_bbox = geom.get("bbox", {})
                    floor = _point(room_bbox.get("min")) if isinstance(room_bbox, dict) else None
                    base = floor[2] if floor else None
                if base is not None and abs(p[2]-(float(base)+height)) > tolerance_m:
                    errors.append({"code": "mounting_height_rule_mismatch", "index": index, "rule_id": rule.get("rule_id"), "message": "Cao độ không khớp rule."})
                elif base is None:
                    warnings.append({"code": "floor_elevation_missing", "index": index, "message": "Không đủ cao độ sàn để đối chiếu rule cao độ."})
            elif rule.get("mounting_height") and not rule.get("mounting_height_m"):
                warnings.append({"code": "mounting_height_not_numeric", "index": index, "rule_id": rule.get("rule_id"), "message": "Rule cao độ chưa ở dạng số; cần kiểm tra thủ công."})
            if _numeric(rule.get("spacing_m", rule.get("spacing"))) is None and rule.get("spacing"):
                warnings.append({"code": "spacing_not_numeric", "index": index, "rule_id": rule.get("rule_id"), "message": "Rule khoảng cách chưa ở dạng số; cần kiểm tra thủ công."})
            spacing = _numeric(rule.get("spacing_m", rule.get("spacing")))
            if spacing is not None:
                for previous, previous_point in seen:
                    if (previous.get("space_id") == sid and previous.get("device_type") == dtype
                            and previous.get("proposed_id") != proposal.get("proposed_id")
                            and hypot(p[0]-previous_point[0], p[1]-previous_point[1]) + tolerance_m < spacing):
                        errors.append({"code": "spacing_rule_mismatch", "index": index, "rule_id": rule.get("rule_id"),
                                       "other_proposed_id": previous.get("proposed_id"), "minimum_spacing_m": spacing,
                                       "message": "Khoảng cách giữa các thiết bị nhỏ hơn rule."})
    counts: dict[tuple[str, str, str], int] = {}
    for proposal in proposals:
        for rid in proposal.get("rule_ids", []):
            key = (str(proposal.get("space_id")), str(proposal.get("device_type")), str(rid))
            counts[key] = counts.get(key, 0) + 1
    for rule in rules:
        rid = rule.get("rule_id")
        if not rid:
            continue
        for (sid, dtype, rule_id), count in counts.items():
            if rule_id != str(rid):
                continue
            quantity = rule.get("quantity_rule") if isinstance(rule.get("quantity_rule"), dict) else {}
            exact = _numeric(rule.get("quantity", quantity.get("count")))
            minimum = _numeric(rule.get("min_qty", quantity.get("min")))
            maximum = _numeric(quantity.get("max"))
            if exact is not None and count != int(exact):
                errors.append({"code": "quantity_rule_mismatch", "space_id": sid, "device_type": dtype, "rule_id": rid,
                               "expected": int(exact), "actual": count, "message": "Số lượng không khớp quy tắc."})
            elif (minimum is not None and count < minimum) or (maximum is not None and count > maximum):
                errors.append({"code": "quantity_range_mismatch", "space_id": sid, "device_type": dtype, "rule_id": rid,
                               "actual": count, "message": "Số lượng ngoài khoảng quy tắc."})
    return {"valid": not errors, "errors": errors, "warnings": warnings, "suggested_fixes": fixes}
