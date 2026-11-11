"""Read-only IFC and placement context tools.

Geometry returned by this module uses metres in a project-local, right-handed
Cartesian coordinate system. When the source does not expose enough geometry,
the tool says so in ``missing_data`` rather than treating it as empty space.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _entity_id(entity: Any) -> str | None:
    value = _get(entity, "GlobalId") or _get(entity, "id")
    if callable(value):
        value = value()
    return str(value) if value is not None else None


def _entity_type(entity: Any) -> str | None:
    value = getattr(entity, "is_a", None)
    if callable(value):
        try:
            return value()
        except TypeError:
            pass
    return _get(entity, "ifc_class") or _get(entity, "type")


def _decode_ifc_text(value: Any) -> Any:
    """Decode IFC STEP Unicode escapes commonly written by Revit/ODA."""
    if not isinstance(value, str):
        return value
    def utf16(match: re.Match[str]) -> str:
        encoded = match.group(1)
        try:
            return bytes.fromhex(encoded).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return match.group(0)
    def utf32(match: re.Match[str]) -> str:
        encoded = match.group(1)
        try:
            return bytes.fromhex(encoded).decode("utf-32-be")
        except (ValueError, UnicodeDecodeError):
            return match.group(0)
    value = re.sub(r"\\X2\\([0-9A-Fa-f]+)\\X0\\", utf16, value)
    value = re.sub(r"\\X4\\([0-9A-Fa-f]+)\\X0\\", utf32, value)
    value = re.sub(r"\\X\\([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), value)
    value = re.sub(r"\\S\\(.)", lambda m: chr(ord(m.group(1))+128), value)
    return value.replace("\\\\", "\\")


def _norm_room(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(_decode_ifc_text(value) or "").casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).replace("đ", "d").split())


def _room_type(space: Any) -> tuple[str | None, str | None]:
    explicit = _decode_ifc_text(_get(space, "space_type"))
    object_type = _decode_ifc_text(_get(space, "ObjectType"))
    label = _decode_ifc_text(_get(space, "LongName") or _get(space, "Name") or _get(space, "name"))
    normalized = _norm_room(label)
    classifications = (
        (("p. ngu", "p ngu", "phong ngu", "ngu so"), "Phòng ngủ"),
        (("wc", "toilet", "nha ve sinh", "phong ve sinh"), "Nhà vệ sinh"),
        (("bep", "kitchen"), "Phòng bếp"),
        (("phong khach", "p. khach"), "Phòng khách"),
        (("gian tho", "phong tho"), "Phòng thờ"),
        (("ban cong", "balcony"), "Ban công"),
        (("gieng troi", "skylight"), "Giếng trời"),
        (("cau thang", "stair"), "Cầu thang"),
        (("san thuong", "roof terrace"), "Sân thượng"),
        (("gara", "garage"), "Gara"),
        (("khu giat", "laundry"), "Khu giặt"),
        (("phong lam viec", "office"), "Phòng làm việc"),
    )
    for terms, canonical in classifications:
        if any(term in normalized for term in terms):
            return canonical, "IFC LongName/Name"
    chosen = explicit or object_type
    if chosen and _norm_room(chosen) not in {"space", "ifcspace", "element"}:
        return str(chosen), "IFC ObjectType"
    return None, None


def _space_members(model: Any, space: Any) -> tuple[list[Any], str]:
    if model is None or not hasattr(model, "by_type"):
        return [], "unavailable"
    sid = _entity_id(space)
    found: dict[str, Any] = {}
    try:
        relationships = model.by_type("IfcRelContainedInSpatialStructure")
    except Exception:
        relationships = []
    for rel in relationships:
        structure = _get(rel, "RelatingStructure")
        if _entity_id(structure) == sid:
            for item in (_get(rel, "RelatedElements", []) or []):
                found[_entity_id(item) or str(id(item))] = item
    # IFC space boundaries provide walls and openings that may not be contained in the space.
    try:
        boundaries = model.by_type("IfcRelSpaceBoundary")
    except Exception:
        boundaries = []
    for rel in boundaries:
        if _entity_id(_get(rel, "RelatingSpace")) == sid:
            element = _get(rel, "RelatedBuildingElement")
            if element is not None:
                found[_entity_id(element) or str(id(element))] = element
    if found:
        return list(found.values()), "ifc_relationship"
    # Many Revit exports omit IfcRelSpaceBoundary. In that case use the space's
    # containing storey to discover candidates; the caller must filter by the
    # actual room geometry and report that the association is inferred.
    storey = _get(space, "storey")
    if storey is None:
        for relation in (_get(space, "Decomposes", []) or []):
            candidate = _get(relation, "RelatingObject")
            if "buildingstorey" in (_entity_type(candidate) or "").casefold():
                storey = candidate
                break
    storey_id = _entity_id(storey)
    if storey_id:
        for rel in relationships:
            if _entity_id(_get(rel, "RelatingStructure")) == storey_id:
                for item in (_get(rel, "RelatedElements", []) or []):
                    found[_entity_id(item) or str(id(item))] = item
    return list(found.values()), "storey_geometry_candidates" if found else "unavailable"


def _bbox_intersects(left: dict[str, Any] | None, right: dict[str, Any] | None, clearance_m: float = 0.0) -> bool | None:
    """Return True/False for two metric bounding boxes, None if unavailable."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    a, b = left.get("bbox", left), right.get("bbox", right)
    if not isinstance(a, dict) or not isinstance(b, dict) or "min" not in a or "max" not in a or "min" not in b or "max" not in b:
        return None
    amin, amax, bmin, bmax = _point3(a["min"]), _point3(a["max"]), _point3(b["min"]), _point3(b["max"])
    if not all((amin, amax, bmin, bmax)):
        return None
    return all(amin[i]-clearance_m <= bmax[i] and amax[i]+clearance_m >= bmin[i] for i in range(3))


def _point3(value: Any) -> tuple[float, float, float] | None:
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


def _location(item: Any, scale: float = 1.0, model: Any = None) -> dict[str, Any] | None:
    location = _get(item, "position") or _get(item, "location")
    if isinstance(location, dict):
        try:
            return {"x": float(location["x"])*scale, "y": float(location["y"])*scale, "z": float(location.get("z", 0))*scale}
        except (KeyError, TypeError, ValueError):
            return location
    placement = _get(item, "ObjectPlacement")
    if placement is not None and model is not None:
        try:
            import ifcopenshell.util.placement
            matrix = ifcopenshell.util.placement.get_local_placement(placement)
            return {"x": float(matrix[0][3])*scale, "y": float(matrix[1][3])*scale, "z": float(matrix[2][3])*scale}
        except Exception:
            pass
    rel = _get(placement, "RelativePlacement")
    point = _get(rel, "Location")
    coords = _get(point, "Coordinates")
    if coords:
        values = list(coords)
        return {"x": values[0]*scale, "y": values[1]*scale, "z": (values[2] if len(values) > 2 else 0.0)*scale}
    return None


def _unit_scale(model: Any) -> float | None:
    try:
        from ifcopenshell.util.unit import calculate_unit_scale
        return float(calculate_unit_scale(model))
    except Exception:
        return None


def _scale_point(value: Any, scale: float) -> Any:
    if isinstance(value, dict) and all(k in value for k in ("x", "y")):
        return {**value, "x": float(value["x"])*scale, "y": float(value["y"])*scale,
                **({"z": float(value["z"])*scale} if value.get("z") is not None else {})}
    if isinstance(value, (list, tuple)) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:3]):
        return [float(x)*scale for x in value]
    return value


def _scale_geometry(value: Any, scale: float, key: str = "") -> Any:
    if key in {"position", "start", "end", "min", "max", "centroid", "origin", "point"}:
        return _scale_point(value, scale)
    if isinstance(value, dict):
        return {k: _scale_geometry(v, scale, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_scale_geometry(v, scale, "point" if key == "polygon_2d" else "") for v in value]
    return value


def _area_m2(space: Any, model: Any, scale: float) -> float | None:
    value = _get(space, "area_m2")
    if value is not None:
        return float(value)
    if model is not None:
        try:
            from ifcopenshell.util.element import get_psets
            psets = get_psets(space, qtos_only=True)
            for qto in psets.values():
                for key in ("NetFloorArea", "GrossFloorArea", "NetArea", "GrossArea"):
                    if qto.get(key) is not None:
                        return float(qto[key]) * scale * scale
        except Exception:
            pass
    return None


def _shape_bbox(model: Any, entity: Any, scale: float) -> dict[str, Any] | None:
    try:
        import ifcopenshell.geom
        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        shape = ifcopenshell.geom.create_shape(settings, entity)
        vertices = list(shape.geometry.verts)
        points = [(vertices[i]*scale, vertices[i+1]*scale, vertices[i+2]*scale) for i in range(0, len(vertices)-2, 3)]
        if not points:
            return None
        lo = [min(p[i] for p in points) for i in range(3)]
        hi = [max(p[i] for p in points) for i in range(3)]
        return {"bbox": {"min": lo, "max": hi}, "centroid": [(lo[i]+hi[i])/2 for i in range(3)]}
    except Exception:
        return None


def ifc_get_placement_context(space_id: str, model: Any = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collect a space and its available hosts/openings/obstacles.

    ``context`` is useful when the IFC reader has already computed geometry.
    ``model`` may be an IfcOpenShell model; callers retain responsibility for
    loading and lifecycle of the IFC file.
    """
    space = None
    if context and str(context.get("space_id", space_id)) == str(space_id):
        space = context
    elif model is not None and hasattr(model, "by_id"):
        # by_id accepts STEP ids, while GlobalId lookup is common in tool calls.
        if str(space_id).isdigit():
            try:
                space = model.by_id(int(space_id))
            except Exception:
                space = None
        if space is None and hasattr(model, "by_type"):
            try:
                space = next((s for s in model.by_type("IfcSpace") if _entity_id(s) == str(space_id)), None)
            except Exception:
                space = None
    if space is None:
        return {"space_id": space_id, "space_type": None, "geometry": None, "walls": [], "ceiling": None,
                "doors": [], "windows": [], "obstacles": [], "coordinate_system": None,
                "missing_data": ["space_not_found", "space_geometry", "walls", "doors", "windows", "obstacles", "coordinate_system"],
                "warnings": ["Không tìm thấy không gian trong IFC/context; chưa thể lập phương án."]}

    name = _decode_ifc_text(_get(space, "LongName") or _get(space, "Name") or _get(space, "name"))
    space_type, space_type_source = _room_type(space)
    if model is not None:
        scale_value = _unit_scale(model)
        unit_known = scale_value is not None
        scale = scale_value if unit_known else 1.0
    else:
        declared_scale = _get(space, "unit_scale_to_m")
        coordinate_system = _get(space, "coordinate_system")
        unit = str(coordinate_system.get("unit", "")).casefold() if isinstance(coordinate_system, dict) else ""
        unit_factors = {"m": 1.0, "metre": 1.0, "meter": 1.0, "metres": 1.0, "meters": 1.0,
                        "mm": 0.001, "millimetre": 0.001, "millimeter": 0.001, "cm": 0.01, "centimetre": 0.01, "centimeter": 0.01,
                        "ft": 0.3048, "foot": 0.3048, "feet": 0.3048, "in": 0.0254, "inch": 0.0254}
        unit_known = declared_scale is not None or unit in unit_factors
        scale = float(declared_scale if declared_scale is not None else unit_factors.get(unit, 1.0))
    geometry = _get(space, "geometry")
    if geometry is None and model is not None:
        geometry = _shape_bbox(model, space, scale)
    result = {"space_id": str(space_id), "space_type": space_type, "space_type_source": space_type_source,
              "name": name, "area_m2": _area_m2(space, model, scale),
              "geometry": geometry, "walls": list(_get(space, "walls", []) or []),
              "ceiling": _get(space, "ceiling"), "doors": list(_get(space, "doors", []) or []),
              "windows": list(_get(space, "windows", []) or []), "obstacles": list(_get(space, "obstacles", []) or []),
              "coordinate_system": _get(space, "coordinate_system"), "unit_scale_to_m": scale, "warnings": []}
    storey = _get(space, "storey")
    if storey is None:
        for relation in (_get(space, "Decomposes", []) or []):
            candidate = _get(relation, "RelatingObject")
            if "buildingstorey" in (_entity_type(candidate) or "").casefold():
                storey = candidate
                break
    result["storey_id"] = _entity_id(storey) if storey is not None else _get(space, "storey_id")
    storey_elevation = _get(space, "storey_elevation_m")
    if storey_elevation is None and storey is not None:
        source_elevation = _get(storey, "Elevation")
        storey_elevation = float(source_elevation)*scale if source_elevation is not None else None
    result["storey_name"] = _decode_ifc_text(_get(storey, "Name")) if storey is not None else _get(space, "storey_name")
    result["storey_elevation_m"] = storey_elevation
    if model is None and scale != 1.0:
        for key in ("geometry", "walls", "ceiling", "doors", "windows", "obstacles"):
            result[key] = _scale_geometry(result[key], scale, key)
    members, membership_method = _space_members(model, space)
    result["membership_method"] = membership_method
    if membership_method == "storey_geometry_candidates":
        result["warnings"].append("IFC không có IfcRelSpaceBoundary; đối tượng cùng tầng được gắn phòng theo giao nhau bounding box, cần kỹ sư rà soát.")
        if not result.get("geometry"):
            result["warnings"].append("Thiếu geometry phòng nên không thể lọc chính xác đối tượng cùng tầng.")
    if members:
        for item in members:
            kind = (_entity_type(item) or "").lower()
            item_geometry = _shape_bbox(model, item, scale)
            if membership_method == "storey_geometry_candidates":
                overlap = _bbox_intersects(result.get("geometry"), item_geometry)
                if overlap is False:
                    continue
                if overlap is None:
                    result["warnings"].append(f"Không đủ hình học để xác nhận đối tượng {_entity_id(item)} thuộc phòng này.")
                    if not result.get("geometry"):
                        continue
            record = {"element_id": _entity_id(item), "ifc_class": _entity_type(item), "name": _get(item, "Name"),
                      "position": _location(item, scale, model), "object_type": _get(item, "ObjectType"), "geometry": item_geometry}
            record["predefined_type"] = _get(item, "PredefinedType")
            if "door" in kind:
                # IFC OperationType describes the door operation (e.g. single
                # swing left), not a world-coordinate swing vector. Preserve it
                # separately; only expose a direction when the source provides
                # an explicit direction value.
                record["operation_type"] = _get(item, "OperationType")
                record["opening_direction"] = _get(item, "opening_direction") or _get(item, "OpeningDirection")
                result["doors"].append(record)
            elif "window" in kind:
                result["windows"].append(record)
            elif "covering" in kind and "ceiling" in str(record["predefined_type"] or "").casefold():
                if result["ceiling"] is None:
                    result["ceiling"] = record
                else:
                    result["warnings"].append("Nhiều bề mặt trần được liên kết; cần chọn bề mặt lắp thiết bị.")
            elif "slab" in kind and "ceiling" in str(record["predefined_type"] or "").casefold():
                if result["ceiling"] is None:
                    result["ceiling"] = record
                else:
                    result["warnings"].append("Nhiều bề mặt trần được liên kết; cần chọn bề mặt lắp thiết bị.")
            elif "wall" in kind or "slab" in kind:
                result["walls" if "wall" in kind else "obstacles"].append(record)
            elif any(t in kind for t in ("column", "furnishing", "furniture", "buildingelementproxy")):
                result["obstacles"].append(record)
    missing = []
    for key in ("geometry", "walls", "ceiling", "doors", "windows", "obstacles"):
        if not result.get(key):
            missing.append(key)
    if result["walls"] and not any(w.get("geometry") or w.get("bbox") or (w.get("start") and w.get("end")) for w in result["walls"] if isinstance(w, dict)):
        missing.append("wall_geometry")
    if any(not d.get("opening_direction") for d in result["doors"] if isinstance(d, dict)):
        missing.append("door_opening_direction")
    if any(not (x.get("geometry") or x.get("bbox")) for group in ("doors", "windows", "obstacles") for x in result[group] if isinstance(x, dict)):
        missing.append("opening_or_obstacle_geometry")
    if not result.get("coordinate_system"):
        missing.append("coordinate_system")
    if not unit_known:
        missing.append("unit_conversion")
    if result.get("area_m2") is None:
        missing.append("area_m2")
    if not result.get("space_type"):
        missing.append("space_type")
    if isinstance(result.get("geometry"), dict) and result["geometry"].get("bbox") and not result["geometry"].get("polygon_2d"):
        missing.append("room_polygon_2d")
    # A bounding-box footprint can help a reviewer understand room scale, but
    # is not a substitute for an IFC quantity or the actual room boundary.
    bbox = result.get("geometry", {}).get("bbox") if isinstance(result.get("geometry"), dict) else None
    if isinstance(bbox, dict) and bbox.get("min") and bbox.get("max"):
        low, high = _point3(bbox["min"]), _point3(bbox["max"])
        if low and high:
            result["area_bbox_estimate_m2"] = round(max(0.0, high[0]-low[0]) * max(0.0, high[1]-low[1]), 3)
            result["area_bbox_estimate_note"] = "Ước lượng theo footprint hộp bao; không thay cho diện tích QTO hoặc biên phòng thực."
    result["area_source"] = "IFC/context area value" if result.get("area_m2") is not None else None
    result["geometry_quality"] = (
        "ifc_geometry_bbox" if isinstance(result.get("geometry"), dict) and result["geometry"].get("bbox")
        else "provided_context" if result.get("geometry") else "unavailable"
    )
    result["coordinate_frame"] = "IFC project-local coordinates; no geographic transform applied"
    result["missing_data"] = missing
    existing_warnings = list(result.get("warnings") or [])
    result["warnings"] = existing_warnings + (["Một số dữ liệu IFC chưa có: " + ", ".join(missing) + "."] if missing else [])
    if unit_known:
        source_cs = result["coordinate_system"] if isinstance(result["coordinate_system"], dict) else {"kind": result["coordinate_system"] or "IFC project coordinates"}
        result["coordinate_system"] = {**source_cs, "unit": "metre", "scale_applied": scale}
    else:
        result["coordinate_system"] = result["coordinate_system"] or {"kind": "IFC project coordinates", "unit": "unknown", "scale_applied": result["unit_scale_to_m"]}
    return result


def ifc_list_placed_devices(space_id: str | None = None, storey_id: str | None = None,
                            placements: list[dict[str, Any]] | None = None, model: Any = None) -> dict[str, Any]:
    """List electrical devices from the project's placement registry or IFC."""
    devices = list(placements or [])
    model_scale = _unit_scale(model) if model is not None else 1.0
    if model is not None and hasattr(model, "by_type"):
        for ifc_class in ("IfcLightFixture", "IfcOutlet", "IfcSwitchingDevice", "IfcElectricAppliance"):
            try:
                entities = model.by_type(ifc_class)
            except Exception:
                continue
            for entity in entities:
                devices.append({"element_id": _entity_id(entity), "device_type": ifc_class,
                                "space_id": _get(entity, "space_id"), "position": _location(entity, model_scale or 1.0, model), "properties": {"name": _get(entity, "Name")}})
    if space_id is not None:
        devices = [d for d in devices if str(d.get("space_id")) == str(space_id)]
    if storey_id is not None:
        devices = [d for d in devices if str(d.get("storey_id")) == str(storey_id)]
    deduplicated: dict[str, dict[str, Any]] = {}
    anonymous = []
    for device in devices:
        element_id = device.get("element_id")
        if not element_id:
            anonymous.append(device)
        elif element_id not in deduplicated or len(device) > len(deduplicated[element_id]):
            deduplicated[element_id] = device
    devices = list(deduplicated.values()) + anonymous
    warnings = ["Không xác định được đơn vị IFC để chuẩn hóa tọa độ thiết bị."] if model is not None and model_scale is None else []
    return {"devices": devices, "count": len(devices), "warnings": warnings}
