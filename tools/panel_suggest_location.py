"""T12: sinh và kiểm tra vị trí tủ nổi trên mặt tường phẳng, tọa độ model_world/m.

Đầu vào hình học phải là polygon/mặt tường thực đã chuẩn hóa bởi backend hoặc
kỹ sư xác nhận. Không chuyển bbox IFC thành mặt lắp hợp lệ. Vùng quét cửa phải
được đưa vào obstacles; bộ kiểm tra dùng khối lăng trụ theo polygon XY và cao độ.
"""

import hashlib
import json
import math
from collections import Counter

from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union


def _number(value, name, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} phải là số hữu hạn.")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} phải {'lớn hơn' if positive else 'không nhỏ hơn'} 0.")
    return float(value)


def _point(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"Thiếu tọa độ {name}.")
    values = [value.get(axis) for axis in ("x", "y", "z")]
    if any(type(number) not in (int, float) or not math.isfinite(number) for number in values):
        raise ValueError(f"Tọa độ {name} phải có x/y/z hữu hạn.")
    return values


def _polygon(data, name):
    """Giữ nguyên lỗ polygon, không tự sửa hình học lỗi hoặc thay bằng hộp bao."""
    try:
        coordinates = [*data["shell"], *(point for hole in data.get("holes", []) for point in hole)]
        if any(
            len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in p)
            for p in coordinates
        ):
            raise ValueError("Tọa độ polygon phải có hai số hữu hạn.")
        polygon = Polygon(data["shell"], data.get("holes", []))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Polygon {name} không đúng định dạng.") from error
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ValueError(f"Polygon {name} rỗng hoặc không hợp lệ.")
    return polygon


def _footprint(origin, tangent, normal, width, near, far):
    return Polygon(
        [
            (origin[0] + tangent[0] * u + normal[0] * v, origin[1] + tangent[1] * u + normal[1] * v)
            for u, v in ((-width / 2, near), (width / 2, near), (width / 2, far), (-width / 2, far))
        ]
    )


def _overlap(footprint, low, high, obstacles, tolerance):
    for obstacle in obstacles:
        if min(high, obstacle["high"]) - max(low, obstacle["low"]) <= tolerance:
            continue
        if footprint.intersection(obstacle["polygon"]).area > tolerance**2:
            return True
    return False


def panel_suggest_location(
    design_context_id: str,
    panel: dict,
    cabinet: dict,
    geometry_context: dict,
    rules: dict,
    served_loads: list[dict],
    upstream_position_m: dict,
    *,
    riser_position_m: dict | None = None,
    limit: int = 5,
) -> dict:
    """Nhận tủ/cabinet, hình học có revision và quy tắc có nguồn; trả candidate ổn định.

    panel chứa panel_id, proposed_storey_id và source_refs đã ghim.
    geometry_context gồm spaces, wall_faces và obstacles; surface_polygon_uz dùng
    u theo tiếp tuyến mặt tường và z tuyệt đối. Không ghi IFC hoặc lưu phương án.
    Phạm vi đầu tiên hỗ trợ tủ nổi, tường phẳng đứng và vật cản dạng lăng trụ.
    """
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit phải là số nguyên từ 1 đến 50.")
    refs = panel["source_refs"]
    output = {
        "design_context_id": design_context_id,
        "panel_id": panel["panel_id"],
        "source_refs": dict(refs),
        "geometry_revision": geometry_context.get("model_revision"),
        "cabinet_revision": cabinet.get("revision"),
        "status": "blocked",
        "candidates": [],
        "candidate_count": 0,
        "candidate_set_id": None,
        "missing_data": [],
        "warnings": [],
        "rejected_counts_by_reason": {},
        "geometry_method": "planar_wall_and_extruded_polygons",
    }
    for key in ("rule_id", "revision", "source"):
        if not isinstance(rules.get(key), str) or not rules[key].strip():
            raise ValueError(f"Thiếu nguồn quy tắc vị trí: {key}")
    if rules["revision"] != refs["ruleset_revision"]:
        raise ValueError("Revision quy tắc vị trí không khớp context.")
    if panel.get("design_context_id") != design_context_id:
        raise ValueError("Tủ không thuộc context đang thiết kế.")
    if geometry_context.get("model_revision") != refs["model_revision"]:
        raise ValueError("Revision hình học đã cũ.")
    if cabinet.get("revision") != refs["playbook_revision"]:
        raise ValueError("Cabinet không thuộc revision catalog đã ghim.")
    if cabinet.get("access_sides") != ["front"]:
        output["missing_data"].append({"code": "UNSUPPORTED_ACCESS_SIDES"})
    if cabinet.get("mounting_type") != "surface":
        output["missing_data"].append(
            {
                "code": "UNSUPPORTED_MOUNTING_TYPE",
                "message": "Tủ âm cần dữ liệu khoét tường và xác nhận kết cấu.",
            }
        )
    if (
        geometry_context.get("coordinate_system") != "model_world"
        or geometry_context.get("unit") != "m"
    ):
        output["missing_data"].append({"code": "INVALID_COORDINATE_SYSTEM"})
    for field in ("spaces", "wall_faces", "obstacles"):
        if not isinstance(geometry_context.get(field), list):
            output["missing_data"].append({"code": "MISSING_GEOMETRY", "field": field})
    for field in (
        "room_boundaries",
        "wall_surfaces",
        "obstacle_inventory",
        "door_sweeps",
        "access_points",
    ):
        if geometry_context.get("verified", {}).get(field) is not True:
            output["missing_data"].append({"code": "UNVERIFIED_GEOMETRY", "field": field})
    if output["missing_data"]:
        return output
    width = _number(cabinet.get("width_m"), "width_m", True)
    height = _number(cabinet.get("height_m"), "height_m", True)
    depth = _number(cabinet.get("depth_m"), "depth_m", True)
    front = _number(rules.get("front_clearance_m"), "front_clearance_m", True)
    front = max(front, _number(cabinet.get("door_projection_m"), "door_projection_m"))
    operation_height = _number(rules.get("operation_height_m"), "operation_height_m", True)
    side = _number(rules.get("side_clearance_m"), "side_clearance_m")
    access_width = _number(rules.get("access_width_m"), "access_width_m", True)
    bottom_height = _number(rules.get("mounting_bottom_height_m"), "mounting_bottom_height_m")
    step = _number(rules.get("sampling_step_m"), "sampling_step_m", True)
    tolerance = _number(rules.get("tolerance_m"), "tolerance_m", True)
    if tolerance >= min(width, height, depth, step, access_width) / 10:
        raise ValueError("Dung sai quá lớn so với kích thước hoặc bước lấy mẫu.")
    weights = rules["ranking_weights"]
    required_weights = {"load_distance", "upstream_distance", "riser_alignment"}
    if set(weights) != required_weights:
        raise ValueError("ranking_weights cần đủ ba tiêu chí khoảng cách.")
    weights = {key: _number(value, key) for key, value in weights.items()}
    if sum(weights.values()) == 0:
        raise ValueError("Cần ít nhất một trọng số xếp hạng dương.")
    upstream = _point(upstream_position_m, "upstream_position_m")
    riser = _point(riser_position_m, "riser_position_m") if riser_position_m else None
    if weights["riser_alignment"] and riser is None:
        output["missing_data"].append({"code": "MISSING_RISER_POSITION"})
        return output
    loads = []
    for load in served_loads:
        if load.get("weight") is None or load.get("position_m") is None:
            output["warnings"].append(
                {"code": "LOAD_EXCLUDED_FROM_RANKING", "device_id": load["device_id"]}
            )
            continue
        loads.append((_point(load["position_m"], "load"), _number(load["weight"], "weight")))
    weight_basis = rules.get("load_weight_basis")
    if weight_basis not in {"connected_W", "connected_VA"}:
        raise ValueError("Cần dùng một loại trọng số tải thống nhất là W hoặc VA.")
    if weights["load_distance"] and sum(weight for _, weight in loads) == 0:
        output["missing_data"].append({"code": "MISSING_LOAD_WEIGHTS"})
        return output
    spaces = {}
    for space in geometry_context["spaces"]:
        key = space["space_id"]
        if key in spaces:
            raise ValueError("space_id bị trùng.")
        polygon = _polygon(space["polygon_xy"], key)
        spaces[key] = {**space, "polygon": polygon}
    obstacles = []
    for item in geometry_context["obstacles"]:
        low, high = item["z_min_m"], item["z_max_m"]
        if (
            not all(type(v) in (int, float) and math.isfinite(v) for v in (low, high))
            or low >= high
        ):
            raise ValueError("Cao độ vật cản không hợp lệ.")
        obstacles.append(
            {"polygon": _polygon(item["polygon_xy"], item["element_id"]), "low": low, "high": high}
        )
    rejected = Counter()
    candidates = []
    seen_faces = set()
    for face in sorted(geometry_context["wall_faces"], key=lambda item: item["face_id"]):
        if face["face_id"] in seen_faces:
            raise ValueError("face_id bị trùng.")
        seen_faces.add(face["face_id"])
        space = spaces.get(face["space_id"])
        if space is None:
            raise ValueError("Mặt tường tham chiếu phòng không tồn tại.")
        if space["storey_id"] != panel["proposed_storey_id"]:
            continue
        if space.get("panel_allowed") is not True or face.get("mounting_allowed") is not True:
            rejected["SPACE_OR_HOST_NOT_ALLOWED"] += 1
            continue
        if not face.get("host_id"):
            raise ValueError("Thiếu host_id của mặt tường.")
        a, b = _point(face["start_m"], "start_m"), _point(face["end_m"], "end_m")
        if abs(a[2] - b[2]) > tolerance:
            rejected["UNSUPPORTED_WALL_SURFACE"] += 1
            continue
        length = math.dist(a[:2], b[:2])
        if length < width + 2 * side:
            rejected["CABINET_DOES_NOT_FIT"] += 1
            continue
        tangent = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        normal = (-tangent[1], tangent[0])
        surface = _polygon(face["surface_polygon_uz"], face["face_id"])
        floor_z = space["floor_z_m"]
        ceiling_z = space["ceiling_z_m"]
        if (
            not all(type(v) in (int, float) and math.isfinite(v) for v in (floor_z, ceiling_z))
            or ceiling_z <= floor_z
        ):
            raise ValueError("Cao độ sàn/trần không hợp lệ.")
        low = floor_z + bottom_height
        high = low + height
        if high > ceiling_z + tolerance:
            rejected["CABINET_DOES_NOT_FIT"] += 1
            continue
        if floor_z + operation_height > ceiling_z + tolerance:
            rejected["OPERATION_HEIGHT_BLOCKED"] += 1
            continue
        start, end = width / 2 + side, length - width / 2 - side
        count = max(1, math.ceil((end - start) / step))
        if count > 10000:
            raise ValueError("Mặt tường cần quá nhiều điểm; tăng sampling_step_m.")
        samples = {round(start + (end - start) * i / count, 9) for i in range(count + 1)}
        for point, _ in [*loads, (upstream, 0)]:
            projection = (point[0] - a[0]) * tangent[0] + (point[1] - a[1]) * tangent[1]
            samples.add(round(min(end, max(start, projection)), 9))
        for u in sorted(samples):
            face_rect = box(u - width / 2 - side, low, u + width / 2 + side, high)
            if not surface.covers(face_rect):
                rejected["WALL_OPENING_OR_EDGE"] += 1
                continue
            origin = [a[0] + tangent[0] * u, a[1] + tangent[1] * u, low + height / 2]
            body = _footprint(origin, tangent, normal, width, 0, depth)
            operation = _footprint(origin, tangent, normal, width + 2 * side, depth, depth + front)
            if not space["polygon"].buffer(tolerance).covers(body.union(operation)):
                rejected["OUTSIDE_USABLE_ROOM"] += 1
                continue
            if _overlap(body, low, high, obstacles, tolerance):
                rejected["BODY_CLASH"] += 1
                continue
            if _overlap(
                operation, floor_z, max(high, floor_z + operation_height), obstacles, tolerance
            ):
                rejected["OPERATION_SPACE_BLOCKED"] += 1
                continue
            # Tủ và vật cản là vùng chiếm chỗ khi kiểm tra lối tiếp cận trên mặt bằng.
            walking_obstacles = [
                o["polygon"]
                for o in obstacles
                if o["low"] < floor_z + operation_height and o["high"] > floor_z
            ]
            free = space["polygon"].difference(unary_union([body, *walking_obstacles]))
            free = free.buffer(-access_width / 2)
            target = Point(
                origin[0] + normal[0] * (depth + front / 2),
                origin[1] + normal[1] * (depth + front / 2),
            )
            access = _point(space["access_point_m"], "access_point_m")
            components = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []))
            if not any(
                component.covers(target) and component.covers(Point(access[:2]))
                for component in components
            ):
                rejected["ACCESS_PATH_BLOCKED"] += 1
                continue
            total = sum(weight for _, weight in loads)
            distance = (
                sum(math.dist(origin, point) * weight for point, weight in loads) / total
                if total
                else 0
            )
            metrics = {
                "load_distance": distance,
                "upstream_distance": math.dist(origin, upstream),
                "riser_alignment": math.dist(origin[:2], riser[:2]) if riser else 0,
            }
            cost = sum(weights[key] * value for key, value in metrics.items()) / sum(
                weights.values()
            )
            anchor = {"x": round(origin[0], 6), "y": round(origin[1], 6), "z": round(origin[2], 6)}
            yaw = round(math.degrees(math.atan2(tangent[1], tangent[0])) % 360, 6)
            identity = [panel["panel_id"], refs, cabinet, rules, face["face_id"], anchor, yaw]
            digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
            candidates.append(
                {
                    "candidate_id": "candidate-" + digest,
                    "panel_id": panel["panel_id"],
                    "space_id": space["space_id"],
                    "host_id": face["host_id"],
                    "face_id": face["face_id"],
                    "position_m": anchor,
                    "anchor": "back_face_centre",
                    "orientation": {"yaw": yaw},
                    "local_axes": {"x": [*tangent, 0], "y": [*normal, 0], "z": [0, 0, 1]},
                    "mounting_height_m": bottom_height,
                    "mounting_height_reference": "bottom_above_finished_floor",
                    "cabinet_volume": {
                        "footprint_xy": list(body.exterior.coords),
                        "z_min_m": low,
                        "z_max_m": high,
                    },
                    "operation_volume": {
                        "footprint_xy": list(operation.exterior.coords),
                        "z_min_m": floor_z,
                        "z_max_m": max(high, floor_z + operation_height),
                    },
                    "hard_checks": {
                        key: "passed"
                        for key in (
                            "wall_fit",
                            "room_fit",
                            "body_clash",
                            "operation_space",
                            "access_path",
                        )
                    },
                    "ranking_metrics_m": {key: round(value, 6) for key, value in metrics.items()},
                    "cost": round(cost, 6),
                    "rule_ids": [rules["rule_id"]],
                    "reasons": [
                        "Đáp ứng kiểm tra polygon và cao độ trong bộ hình học được cung cấp."
                    ],
                }
            )
    candidates.sort(key=lambda item: (item["cost"], item["candidate_id"]))
    output["candidates"] = candidates[:limit]
    output["candidate_count"] = len(candidates)
    output["rejected_counts_by_reason"] = dict(sorted(rejected.items()))
    output["status"] = ("partial" if output["warnings"] else "ok") if candidates else "no_candidate"
    output["distance_method"] = "euclidean_estimate"
    output["assumptions"] = [
        "Khoảng cách dùng để xếp hạng, cần routing để xác định chiều dài cáp.",
        "Kiểm tra lối đi dùng polygon 2D co theo bề rộng tiếp cận.",
    ]
    canonical = json.dumps(output, sort_keys=True, ensure_ascii=False, allow_nan=False)
    output["candidate_set_id"] = "candidates-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
    return output
