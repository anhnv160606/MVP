"""Tool 7: liệt kê thiết bị đã có trong mô hình IFC."""

import math
import os
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import ifcopenshell.util.element
import ifcopenshell.util.placement

from ifc_engine.reader import IfcReader

_PLAYBOOK_CLIENT = ContextVar("playbook_client", default=None)


@contextmanager
def playbook_client_context(client):
    token = _PLAYBOOK_CLIENT.set(client)
    try:
        yield
    finally:
        _PLAYBOOK_CLIENT.reset(token)


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
                chain
                for chain in chains
                if (
                    space_id is None
                    or any(s.is_a("IfcSpace") and s.GlobalId == space_id for s in chain)
                )
                and (
                    storey_id is None
                    or any(s.is_a("IfcBuildingStorey") and s.GlobalId == storey_id for s in chain)
                )
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

            result.append(
                {
                    "element_id": element.GlobalId,
                    "device_type": element.ObjectType or element.Name or element.is_a(),
                    "space_id": space.GlobalId if space else None,
                    "position": position,
                    "properties": {
                        "ifc_type": element.is_a(),
                        "name": element.Name,
                        "property_sets": ifcopenshell.util.element.get_psets(element),
                    },
                }
            )
    return sorted(result, key=lambda item: item["element_id"])


# Các phần bổ sung dưới đây giữ nguyên tool 7 ở phía trên.


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
        Path("ifc_file/ctr.ifc"),
        Path("ifc_file/A.ifc"),
        Path("ctr.ifc"),
        Path("A.ifc"),
        Path("../ctr.ifc"),
        Path("../A.ifc"),
    ):
        path = project_root / relative_path
        if path.is_file():
            return path
    raise FileNotFoundError(
        "Không tìm thấy IFC; hãy đặt IFC_FILE_PATH hoặc thêm tệp vào ifc_file/."
    )


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
    label = _normalize_text(
        " ".join(str(record.get(key) or "") for key in ("name", "object_type", "predefined_type"))
    )
    if "ifcdoor" in ifc_class:
        return "doors"
    if "ifcwindow" in ifc_class:
        return "windows"
    if "ifcwall" in ifc_class or "ifccurtainwall" in ifc_class:
        return "walls"
    if "ifcslab" in ifc_class or "ifccovering" in ifc_class:
        return "ceiling" if any(token in label for token in ("ceiling", "tran")) else None
    return "obstacles"


def ifc_get_placement_context(
    space_id: str, ifc_file_path: str | Path | None = None
) -> dict[str, Any]:
    """Read room geometry, boundary hosts, openings and known obstacles.

    `space_id` is an IfcSpace GlobalId. The backend binds `ifc_file_path` to
    the current project's IFC; standalone callers may use IFC_FILE_PATH.
    Coordinates and geometry are model-world metres. Bounding boxes are
    approximations, not room polygons or verified mounting surfaces. Inspect
    missing_data before proposing coordinates; never infer absent obstacles.
    This operation does not change the IFC or generate placements.
    """
    import ifcopenshell.util.element

    reader = IfcReader(Path(ifc_file_path) if ifc_file_path else _default_ifc_path())
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

    storey = ifcopenshell.util.element.get_parent(space)
    visited = set()
    while (
        storey is not None and not storey.is_a("IfcBuildingStorey") and storey.id() not in visited
    ):
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
        if (
            relating_space is not None
            and relating_space.GlobalId == space_id
            and element is not None
        ):
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
        "space_type": _space_type_from_name(
            getattr(space, "Name", None), getattr(space, "LongName", None)
        ),
        "geometry": {
            "room_name": getattr(space, "LongName", None) or getattr(space, "Name", None),
            "bbox": bbox,
            "centroid": geometry_source.get("bbox_center"),
            "area_m2": area_m2,
            "area_bbox_estimate_m2": (
                abs((bbox["max"][0] - bbox["min"][0]) * (bbox["max"][1] - bbox["min"][1]))
                if bbox
                else None
            ),
            "area_bbox_estimate_note": "Ước lượng theo hộp bao, không thay cho diện tích đo bóc hoặc biên phòng thực.",
            "geometry_quality": "ifc_geometry_bbox"
            if geometry_source.get("status") == "ok"
            else "unavailable",
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


def _get_supabase_client():
    """Tạo Supabase Client theo cấu hình Settings hiện có của dự án."""
    from config.settings import Settings
    from database.supabase_client import get_supabase_client

    return _PLAYBOOK_CLIENT.get() or get_supabase_client(Settings())


def _playbook_rows(client, table: str) -> list[dict[str, Any]]:
    """Đọc toàn bộ hàng cần tra từ một bảng Playbook."""
    rows = []
    offset, page_size = 0, 500
    key = {
        "quy_tac_doi_tuong": "ma_quy_tac",
        "lop_phu_tai": "ma_lop_phu_tai",
        "lop_dau_cuoi": "ma_lop_dau_cuoi",
    }.get(table, "id")
    while True:
        page = (
            client.table(table)
            .select("*")
            .order(key)
            .range(offset, offset + page_size - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def _room_matches(scope: Any, space_type: str) -> bool:
    scope_text = _normalize_text(scope)
    room_text = _normalize_text(space_type)
    if not scope_text or not room_text:
        return False
    if "cac phong" in scope_text or "cac khong gian" in scope_text:
        return True
    scopes = [part.strip() for part in scope_text.replace(",", ";").split(";")]
    return any(part and (part in room_text or room_text in part) for part in scopes)


def _device_matches(query: str, *values: Any) -> bool:
    query_text = _normalize_text(query)
    if not query_text:
        return False

    cleaned_values = []
    for value in values:
        if value is None:
            continue
        norm = _normalize_text(value)
        if not norm:
            continue
        # 1. Khớp trực tiếp cụm từ nguyên vẹn (hai chiều)
        if query_text in norm or norm in query_text:
            return True
        cleaned_values.append(norm)

    if not cleaned_values:
        return False

    # 2. Nếu không khớp nguyên cụm, yêu cầu TẤT CẢ từ khóa lõi của query phải có mặt
    # (tránh trường hợp "đèn trần" khớp nhầm với "công tắc điều khiển đèn" chỉ vì chung chữ "đèn")
    ignored = {"diem", "cap", "dien", "thiet", "bi", "phong", "cho", "loai", "cac"}
    query_words = {
        word
        for word in query_text.replace(",", " ").replace(";", " ").split()
        if len(word) > 1 and word not in ignored
    }
    if not query_words:
        return False

    value_text = " ".join(cleaned_values).replace(",", " ").replace(";", " ").replace("/", " ")
    value_words = set(value_text.split())

    return query_words.issubset(value_words)


def _row_matches_filters(row: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    for key, expected in (filters or {}).items():
        if expected is None or expected == "":
            continue
        if key not in row:
            return False
        actual = row[key]
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            if actual != expected:
                return False
        elif _normalize_text(expected) not in _normalize_text(actual):
            return False
    return True


def _to_number(value: Any) -> int | float | None:
    """Chuyển giá trị số từ Supabase (int, float, Decimal, chuỗi) về số Python."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _split_keywords(raw: Any) -> list[str]:
    """tu_khoa_nhan_dien là cột mảng text; vẫn chấp nhận chuỗi ngăn cách bằng ; hoặc ,."""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = raw.replace(",", ";").split(";")
    return [str(item).strip() for item in raw if str(item).strip()]


def playbook_get_object_rules(
    device_type: str, space_type: str, project_standard: str
) -> dict[str, Any]:
    """Tool 3: tra quy tắc đối tượng (quy_tac_doi_tuong) và quy tắc phòng (quy_tac_phong).

    Mọi trường trả về đều lấy từ cột có thật trong DB. Các trường quantity_rule,
    spacing, host_type đã bỏ vì không có cột nguồn. Trường không áp dụng cho
    loại quy tắc đó (ví dụ mounting_height của quy tắc đối tượng) để None.
    """
    client = _get_supabase_client()
    rules: list[dict[str, Any]] = []

    for row in _playbook_rows(client, "quy_tac_doi_tuong"):
        keywords = _split_keywords(row.get("tu_khoa_nhan_dien"))
        if not _device_matches(device_type, row.get("ten_quy_tac"), *keywords):
            continue
        if not _room_matches(row.get("khong_gian_thuong_gap"), space_type):
            continue
        rules.append(
            {
                "rule_id": row.get("ma_quy_tac"),
                "rule_name": row.get("ten_quy_tac"),
                "rule_type": "object",
                "terminal_class_id": row.get("lop_dau_cuoi_ma"),
                "ifc_match_field": row.get("truong_ifc_doi_chieu"),
                "match_method": row.get("phuong_thuc_khop"),
                "keywords": keywords,
                "placement_principle": None,
                "mounting_height": None,
                "constraints": [
                    value
                    for value in (row.get("dieu_kien_bo_sung"), row.get("xu_ly_canh_bao"))
                    if value
                ],
                "source": "Playbook > Quy tắc đối tượng",
                "source_status": row.get("tinh_trang_kiem_chung"),
                "scope": row.get("khong_gian_thuong_gap"),
                "standard_applicability": "Chưa được nêu trong nguồn",
                "raw_rule": row,
            }
        )

    for row in _playbook_rows(client, "quy_tac_phong"):
        if not _room_matches(row.get("loai_khong_gian"), space_type):
            continue
        if not _device_matches(device_type, row.get("thiet_bi_thuong_xet")):
            continue
        rules.append(
            {
                "rule_id": f"room:{row.get('id')}",
                "rule_name": row.get("loai_khong_gian"),
                "rule_type": "room",
                "terminal_class_id": None,
                "ifc_match_field": None,
                "match_method": None,
                "keywords": [],
                "placement_principle": row.get("nguyen_tac_bo_tri"),
                "mounting_height": row.get("cao_do_lap_dat"),
                "constraints": [
                    value
                    for value in (row.get("kiem_tra_bo_sung"), row.get("gioi_han_canh_bao"))
                    if value
                ],
                "source": "Playbook > Quy tắc phòng",
                "source_status": row.get("loai_du_lieu"),
                "scope": row.get("loai_khong_gian"),
                "standard_applicability": "Chưa được nêu trong nguồn",
                "raw_rule": row,
            }
        )
    return {"rules": rules}


def playbook_query_devices(
    category: str, filters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Find existing catalog modules, breakers and connection devices by category.

    category matches normalized Vietnamese/English device keywords. filters maps
    source column names to exact/substring values. Returns module_id/name, specs,
    optional ifc_class, rated_power and geometry_ref; null means missing source
    data, not a default. Inspect specs before treating any rating as consumption.
    This lookup does not create IFC instances or invent unavailable catalog IDs.
    """
    client = _get_supabase_client()
    result = []
    for table, key in (
        ("mo_dun_thiet_bi", "ten_cau_hinh"),
        ("thiet_bi_dong_cat", "ma_cau_hinh_mo_ta"),
        ("dau_noi_dien", "ten_dau_noi"),
    ):
        for row in _playbook_rows(client, table):
            if not _device_matches(category, *row.values()):
                continue
            if not _row_matches_filters(row, filters):
                continue
            result.append(
                {
                    "module_id": row.get(key),
                    "name": row.get(key),
                    "ifc_class": row.get("ifc_class"),
                    "specs": row,
                    "rated_power": row.get("rated_power_w"),
                    "geometry_ref": row.get("geometry_ref"),
                }
            )
    return result


def playbook_get_load_class(space_type: str, device_category: str) -> dict[str, Any]:
    """Resolve a unique Playbook load class from room usage and device category.

    space_type/device_category are semantic labels, not IFC IDs. Returns the
    source load_class_id, load_class_name and load_type. No or ambiguous matches
    return null fields. A load class is not a demand/diversity factor; this tool
    does not estimate power or choose a breaker.
    """
    client = _get_supabase_client()
    category = _normalize_text(device_category)
    aliases = (
        (("den", "lighting"), "chieu sang"),
        (("o cam", "socket", "outlet"), "o cam"),
        (("dieu hoa", "air conditioner", "ac"), "dieu hoa"),
        (("binh nong lanh", "water heater"), "binh nuoc nong"),
        (("may bom", "pump"), "may bom"),
    )
    category = next(
        (
            target
            for keys, target in aliases
            if any(category == key or category.startswith(f"{key} ") for key in keys)
        ),
        category,
    )
    room = _normalize_text(space_type)
    candidates = []
    for row in _playbook_rows(client, "lop_phu_tai"):
        searchable = _normalize_text(
            f"{row.get('ten_lop_phu_tai', '')} {row.get('nhom_phu_tai', '')}"
        )
        if not category or category not in searchable:
            continue
        candidates.append((1 + (2 if room and room in searchable else 0), row))
    if not candidates:
        return {"load_class_id": None, "load_class_name": None, "load_type": None}
    best_score = max(score for score, _ in candidates)
    best = [row for score, row in candidates if score == best_score]
    if len(best) != 1:
        return {"load_class_id": None, "load_class_name": None, "load_type": None}
    row = best[0]
    return {
        "load_class_id": row.get("ma_lop_phu_tai"),
        "load_class_name": row.get("ten_lop_phu_tai"),
        "load_type": row.get("nhom_phu_tai"),
    }


def _supply_phases(supply_type: Any) -> list[str]:
    """Suy ra danh sách pha từ cột kieu_cap_dien (ví dụ 'Một pha hai dây hoặc ba pha bốn dây')."""
    supply = _normalize_text(supply_type)
    phases = []
    if "mot pha" in supply:
        phases.append("1 pha")
    if "ba pha" in supply:
        phases.append("3 pha")
    return phases


def _requested_phases(requested: Any) -> set[str]:
    """Chuẩn hóa yêu cầu pha của người gọi về '1 pha' / '3 pha'."""
    items = requested if isinstance(requested, list) else [requested]
    normalized = set()
    for phase in items:
        text = _normalize_text(phase)
        if phase == 1 or "1 pha" in text or "mot pha" in text:
            normalized.add("1 pha")
        elif phase == 3 or "3 pha" in text or "ba pha" in text:
            normalized.add("3 pha")
        else:
            normalized.add(text)
    return normalized


def playbook_get_terminal_class(
    device_type: str, connection_requirements: dict[str, Any]
) -> dict[str, Any]:
    """Tool 6: tra lớp đầu cuối (lop_dau_cuoi) và đặc điểm kết nối trong Playbook.

    Điện áp lấy từ cột dien_ap_v. Với lớp hỗ trợ cả một pha lẫn ba pha, giá trị
    này là một số duy nhất (hiện là dữ liệu test), nên missing_data chỉ báo thiếu
    điện áp khi cột để trống.
    """
    client = _get_supabase_client()
    requirements = (
        connection_requirements
        if isinstance(connection_requirements, dict)
        else {"description": connection_requirements}
    )
    loads = {row.get("ma_lop_phu_tai"): row for row in _playbook_rows(client, "lop_phu_tai")}

    candidates = []
    for row in _playbook_rows(client, "lop_dau_cuoi"):
        load = loads.get(row.get("lop_phu_tai_ma"), {})
        if not _device_matches(
            device_type, row.get("ten_lop_dau_cuoi"), load.get("ten_lop_phu_tai")
        ):
            continue
        phases = _supply_phases(row.get("kieu_cap_dien"))
        requested_phases = requirements.get("phases")
        if requested_phases and not _requested_phases(requested_phases).issubset(set(phases)):
            continue
        connection = requirements.get("connection_type")
        if connection and _normalize_text(connection) not in _normalize_text(
            row.get("kieu_diem_ket_noi")
        ):
            continue
        voltage = requirements.get("voltage")
        if voltage is not None and _to_number(row.get("dien_ap_v")) != voltage:
            continue
        candidates.append((row, phases))

    if len(candidates) != 1:
        return {
            "terminal_class_id": None,
            "voltage": None,
            "phases": [],
            "connection_specs": {"missing_data": ["unique_terminal_class", "voltage"]},
        }

    row, phases = candidates[0]
    voltage = _to_number(row.get("dien_ap_v"))
    return {
        "terminal_class_id": row.get("ma_lop_dau_cuoi"),
        "voltage": voltage,
        "phases": phases,
        "connection_specs": {
            "power_supply_type": row.get("kieu_cap_dien"),
            "connection_type": row.get("kieu_diem_ket_noi"),
            "circuit_group": row.get("nhom_mach"),
            "applicable_space": row.get("khong_gian_ap_dung"),
            "power_factor": _to_number(row.get("he_so_cong_suat_tam")),
            "voltage_drop_limit_percent": _to_number(row.get("gioi_han_sut_ap_pct")),
            "notes": row.get("ghi_chu"),
            "source_status": row.get("tinh_trang_kiem_chung"),
            "missing_data": [] if voltage is not None else ["voltage"],
        },
    }
