"""Tool 7: liệt kê thiết bị đã có trong mô hình IFC."""

from pathlib import Path
from typing import Any

import ifcopenshell.util.element
import ifcopenshell.util.placement

from ifc_engine.reader import IfcReader


DEFAULT_DEVICE_TYPES = (
    "IfcFlowTerminal",
    "IfcDistributionControlElement",
    "IfcEnergyConversionDevice",
    "IfcFlowController",
    "IfcFlowMovingDevice",
    "IfcElectricDistributionBoard",
    "IfcBuildingElementProxy",
    "IfcFurniture",
    "IfcSystemFurnitureElement",
)


def ifc_list_placed_devices(
    ifc_file_path: str | Path,
    space_id: str | None = None,
    storey_id: str | None = None,
    ifc_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Liệt kê thiết bị và đối tượng hiện có trong IFC để lập phương án bố trí.

    Gọi trước khi đề xuất thiết bị mới để xem hiện trạng. Lọc theo quan hệ
    chứa/tham chiếu không gian và cấu trúc tầng của IFC; không suy ra phòng
    từ tọa độ hoặc bounding box. Kết quả có thể gồm nội thất và proxy.

    Args:
        ifc_file_path: Đường dẫn đến file IFC cần đọc, bắt buộc.
        space_id: GlobalId của IfcSpace; None để không lọc theo phòng.
        storey_id: GlobalId của IfcBuildingStorey; None để không lọc theo tầng.
            Nếu truyền cả hai, một chuỗi quan hệ không gian phải thỏa cả hai.
        ifc_types: Danh sách tên lớp IFC cần lấy, gồm cả lớp con. None dùng
            DEFAULT_DEVICE_TYPES (các nhóm MEP, proxy và nội thất); [] trả [].

    Returns:
        Danh sách sắp xếp theo element_id; mỗi phần tử gồm:
        - element_id: GlobalId của đối tượng IFC.
        - device_type: ObjectType, hoặc Name, hoặc tên lớp IFC; là thông tin
          gốc, chưa chắc là mã loại thiết bị được chuẩn hóa.
        - space_id: GlobalId phòng theo quan hệ IFC, hoặc None nếu không rõ.
        - position: {x, y, z} của gốc ObjectPlacement trong hệ tọa độ model,
          đơn vị mét; None nếu thiếu placement. Đây không phải tâm hình học.
        - properties: {ifc_type, name, property_sets} từ IFC.

    Raises:
        ValueError: GlobalId phòng/tầng sai loại hoặc ifc_types chứa lớp không
            có trong schema IFC. Lỗi mở file hoặc đọc IFC có thể được truyền ra.

    Lưu ý: nếu lọc theo phòng, đối tượng không có quan hệ phòng phù hợp sẽ
    không xuất hiện; danh sách rỗng không chứng minh phòng không có thiết bị.
    """
    reader = IfcReader(Path(ifc_file_path))
    model = reader.model
    if space_id is not None:
        reader._get_element(space_id, "IfcSpace")
    if storey_id is not None:
        reader._get_element(storey_id, "IfcBuildingStorey")

    def ancestors(spatial):
        seen = set()
        while spatial is not None and spatial.id() not in seen:
            seen.add(spatial.id())
            yield spatial
            spatial = ifcopenshell.util.element.get_aggregate(spatial)

    result = []
    seen_elements = set()
    for ifc_type in DEFAULT_DEVICE_TYPES if ifc_types is None else ifc_types:
        try:
            elements = model.by_type(ifc_type)
        except RuntimeError:
            if ifc_types is not None:
                raise ValueError(f"Lớp IFC không tồn tại trong schema: {ifc_type}") from None
            continue
        for element in elements:
            if element.GlobalId in seen_elements:
                continue
            seen_elements.add(element.GlobalId)
            container = ifcopenshell.util.element.get_container(element)
            spatial_roots = [container] if container is not None else []
            spatial_roots.extend(
                rel.RelatingStructure
                for rel in getattr(element, "ReferencedInStructures", ())
                if rel.RelatingStructure is not None
            )
            chains = [list(ancestors(root)) for root in spatial_roots]
            matching_chains = [
                chain for chain in chains
                if (space_id is None or any(s.is_a("IfcSpace") and s.GlobalId == space_id for s in chain))
                and (storey_id is None or any(s.is_a("IfcBuildingStorey") and s.GlobalId == storey_id for s in chain))
            ]
            if (space_id is not None or storey_id is not None) and not matching_chains:
                continue
            chain = matching_chains[0] if matching_chains else []
            space = next((s for s in chain if s.is_a("IfcSpace")), None)

            position = None
            if getattr(element, "ObjectPlacement", None) is not None:
                matrix = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
                position = {
                    axis: float(matrix[index, 3]) * reader.length_scale
                    for index, axis in enumerate(("x", "y", "z"))
                }

            result.append({
                "element_id": element.GlobalId,
                "device_type": element.ObjectType or element.Name or element.is_a(),
                "space_id": space.GlobalId if space else None,
                "position": position,
                "properties": {
                    "ifc_type": element.is_a(),
                    "name": element.Name,
                    "property_sets": ifcopenshell.util.element.get_psets(element),
                },
            })
    return sorted(result, key=lambda item: item["element_id"])


# Các phần bổ sung dưới đây giữ nguyên tool 7 ở phía trên.
import os
import unicodedata


def _default_ifc_path() -> Path:
    """Tìm tệp IFC theo cấu hình hoặc các vị trí mặc định của dự án."""
    configured = os.getenv("IFC_FILE_PATH")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Không tìm thấy IFC_FILE_PATH: {path}")
        return path

    project_root = Path(__file__).resolve().parents[1]
    for relative_path in (
        Path("ifc_file/ctr.ifc"), Path("ifc_file/A.ifc"),
        Path("ctr.ifc"), Path("A.ifc"), Path("../ctr.ifc"), Path("../A.ifc"),
    ):
        path = project_root / relative_path
        if path.is_file():
            return path
    raise FileNotFoundError("Không tìm thấy IFC; hãy đặt IFC_FILE_PATH hoặc thêm tệp vào ifc_file/.")


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in text if not unicodedata.combining(char)).replace("đ", "d")


def _space_type_from_name(name: str | None, long_name: str | None) -> str | None:
    label = _normalize_text(f"{long_name or ''} {name or ''}")
    kinds = (
        (("phong ngu", "bedroom"), "Phòng ngủ"),
        (("phong khach", "living"), "Phòng khách"),
        (("bep", "kitchen"), "Bếp"),
        (("phong tam", "ve sinh", "wc", "toilet", "bathroom"), "Phòng vệ sinh"),
        (("hanh lang", "corridor", "hallway"), "Hành lang"),
        (("gara", "garage"), "Gara"),
        (("phong lam viec", "study", "office"), "Phòng làm việc"),
    )
    return next((kind for tokens, kind in kinds if any(token in label for token in tokens)), None)


def _element_geometry(element) -> dict[str, Any] | None:
    """Lấy hộp bao hình học IFC; đỉnh IfcOpenShell đã ở đơn vị mét."""
    import ifcopenshell.geom
    import ifcopenshell.util.shape

    if getattr(element, "Representation", None) is None:
        return None
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
        vertices = ifcopenshell.util.shape.get_vertices(shape.geometry)
    except RuntimeError:
        return None
    if len(vertices) == 0:
        return None
    minimum = vertices.min(axis=0)
    maximum = vertices.max(axis=0)
    return {
        "bbox": {"min": minimum.tolist(), "max": maximum.tolist()},
        "centroid": ((minimum + maximum) / 2).tolist(),
    }


def _ifc_element_record(element, length_scale: float) -> dict[str, Any]:
    import ifcopenshell.util.placement

    placement = getattr(element, "ObjectPlacement", None)
    position = None
    if placement is not None:
        try:
            matrix = ifcopenshell.util.placement.get_local_placement(placement)
            position = {
                axis: float(matrix[index, 3]) * length_scale
                for index, axis in enumerate(("x", "y", "z"))
            }
        except RuntimeError:
            position = None
    return {
        "element_id": getattr(element, "GlobalId", None),
        "ifc_class": element.is_a(),
        "name": getattr(element, "Name", None),
        "object_type": getattr(element, "ObjectType", None),
        "predefined_type": getattr(element, "PredefinedType", None),
        "position": position,
        "geometry": _element_geometry(element),
        "operation_type": getattr(element, "OperationType", None),
        "opening_direction": None,
    }


def _placement_category(record: dict[str, Any]) -> str | None:
    ifc_class = str(record.get("ifc_class") or "").casefold()
    label = _normalize_text(" ".join(str(record.get(key) or "") for key in (
        "name", "object_type", "predefined_type"
    )))
    if "ifcdoor" in ifc_class:
        return "doors"
    if "ifcwindow" in ifc_class:
        return "windows"
    if "ifcwall" in ifc_class or "ifccurtainwall" in ifc_class:
        return "walls"
    if "ifcslab" in ifc_class or "ifccovering" in ifc_class:
        return "ceiling" if any(token in label for token in ("ceiling", "tran")) else None
    return "obstacles"


def ifc_get_placement_context(space_id: str) -> dict[str, Any]:
    """Tool 1: đọc ngữ cảnh bố trí từ IFC; đầu vào duy nhất là space_id."""
    import ifcopenshell.util.element

    reader = IfcReader(_default_ifc_path())
    space = reader._get_element(space_id, "IfcSpace")
    model = reader.model
    geometry_source = reader.get_space_geometry(space_id)
    bbox = geometry_source.get("bbox")

    # Tra diện tích định lượng nếu IFC có QTO; không thay bằng hộp bao.
    area_m2 = None
    try:
        quantities = ifcopenshell.util.element.get_psets(space, qtos_only=True)
        for quantity_set in quantities.values():
            for key in ("NetFloorArea", "GrossFloorArea", "Area"):
                value = quantity_set.get(key)
                if isinstance(value, (int, float)):
                    area_m2 = float(value) * reader.length_scale**2
                    break
            if area_m2 is not None:
                break
    except (AttributeError, TypeError):
        pass

    storey = ifcopenshell.util.element.get_container(space)
    visited = set()
    while storey is not None and not storey.is_a("IfcBuildingStorey") and storey.id() not in visited:
        visited.add(storey.id())
        storey = ifcopenshell.util.element.get_aggregate(storey)
    storey_name = getattr(storey, "Name", None) if storey is not None else None
    storey_elevation = (
        float(storey.Elevation) * reader.length_scale
        if storey is not None and getattr(storey, "Elevation", None) is not None
        else None
    )

    boundary = []
    for relation in model.by_type("IfcRelSpaceBoundary"):
        relating_space = getattr(relation, "RelatingSpace", None)
        element = getattr(relation, "RelatedBuildingElement", None)
        if relating_space is not None and relating_space.GlobalId == space_id and element is not None:
            boundary.append(element)

    # Lần theo quan hệ tường, lỗ mở và cửa/cửa sổ lấp đầy lỗ mở.
    boundary_hosts = {element.id() for element in boundary}
    fillings = {}
    for relation in model.by_type("IfcRelFillsElement"):
        opening = getattr(relation, "RelatingOpeningElement", None)
        element = getattr(relation, "RelatedBuildingElement", None)
        if opening is not None and element is not None:
            fillings.setdefault(opening.id(), []).append(element)
    for relation in model.by_type("IfcRelVoidsElement"):
        host = getattr(relation, "RelatingBuildingElement", None)
        opening = getattr(relation, "RelatedOpeningElement", None)
        if host is not None and opening is not None and host.id() in boundary_hosts:
            boundary.extend(fillings.get(opening.id(), ()))

    contained = []
    for relation in model.by_type("IfcRelContainedInSpatialStructure"):
        structure = getattr(relation, "RelatingStructure", None)
        if structure is not None and structure.GlobalId == space_id:
            contained.extend(getattr(relation, "RelatedElements", ()) or ())

    result: dict[str, Any] = {
        "space_type": _space_type_from_name(getattr(space, "Name", None), getattr(space, "LongName", None)),
        "geometry": {
            "room_name": getattr(space, "LongName", None) or getattr(space, "Name", None),
            "bbox": bbox,
            "centroid": geometry_source.get("bbox_center"),
            "area_m2": area_m2,
            "area_bbox_estimate_m2": (
                abs((bbox["max"][0] - bbox["min"][0]) * (bbox["max"][1] - bbox["min"][1]))
                if bbox else None
            ),
            "area_bbox_estimate_note": "Ước lượng theo hộp bao, không thay cho diện tích đo bóc hoặc biên phòng thực.",
            "geometry_quality": "ifc_geometry_bbox" if geometry_source.get("status") == "ok" else "unavailable",
            "storey_name": storey_name,
            "storey_elevation_m": storey_elevation,
        },
        "walls": [],
        "ceiling": [],
        "doors": [],
        "windows": [],
        "obstacles": [],
        "coordinate_system": {
            "kind": "Tọa độ dự án IFC",
            "unit": "metre" if reader.length_scale > 0 else None,
            "scale_applied": reader.length_scale,
            "frame": "Hệ tọa độ cục bộ dự án IFC; chưa chuyển sang tọa độ địa lý",
            "elevation_m": storey_elevation,
        },
        "missing_data": [],
    }

    seen = set()
    for source, elements in (("boundary", boundary), ("contained", contained)):
        for element in elements:
            element_id = getattr(element, "GlobalId", None)
            if not element_id or element_id in seen:
                continue
            seen.add(element_id)
            record = _ifc_element_record(element, reader.length_scale)
            category = _placement_category(record)
            if category is None:
                continue
            if source == "contained" and category not in ("doors", "windows"):
                category = "obstacles"
            result[category].append(record)

    missing = result["missing_data"]
    if result["space_type"] is None:
        missing.append("space_type")
    if geometry_source.get("status") != "ok":
        missing.append("geometry")
    if area_m2 is None:
        missing.append("area_m2")
    missing.append("room_polygon_2d")
    if storey is None:
        missing.append("storey")
    elif storey_elevation is None:
        missing.append("storey_elevation")
    for key in ("walls", "ceiling", "doors", "windows", "obstacles"):
        if not result[key]:
            missing.append(key)
        elif any(item.get("geometry") is None for item in result[key]):
            missing.append(f"{key}_geometry")
    if result["doors"] and any(not item.get("opening_direction") for item in result["doors"]):
        missing.append("door_opening_direction")
    if result["coordinate_system"]["unit"] is None:
        missing.append("coordinate_system")
    return result
