"""Read project spaces and storeys from an IFC model."""

from dataclasses import dataclass, field
from hashlib import file_digest
from pathlib import Path
from typing import Any
import unicodedata

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.shape
import ifcopenshell.util.unit


@dataclass(frozen=True)
class SpatialIndex:
    revision: str
    rooms: dict[str, str | None]
    floors: frozenset[str]
    space_names: dict[str, str] = field(default_factory=dict)
    storey_names: dict[str, str] = field(default_factory=dict)


def read_spaces(path: str | Path) -> SpatialIndex:
    """Build a validated room/storey index and pin it to the IFC file hash."""
    from ifcopenshell.util.element import get_parent

    path = Path(path)
    with path.open("rb") as stream:
        revision = file_digest(stream, "sha256").hexdigest()
    model = ifcopenshell.open(str(path))
    storey_names = {
        storey.GlobalId: storey.Name or storey.GlobalId
        for storey in model.by_type("IfcBuildingStorey")
    }
    rooms: dict[str, str | None] = {}
    space_names: dict[str, str] = {}
    for room in model.by_type("IfcSpace"):
        parent = get_parent(room)
        visited = {room.id()}
        while parent and not parent.is_a("IfcBuildingStorey"):
            if parent.id() in visited:
                raise ValueError(f"Cyclic IFC containment for room {room.GlobalId}")
            visited.add(parent.id())
            parent = get_parent(parent)
        if room.GlobalId in rooms:
            raise ValueError(f"Duplicate IFC room GlobalId {room.GlobalId}")
        rooms[room.GlobalId] = parent.GlobalId if parent else None
        space_names[room.GlobalId] = room.LongName or room.Name or room.GlobalId
    with path.open("rb") as stream:
        if revision != file_digest(stream, "sha256").hexdigest():
            raise ValueError("IFC changed during read")
    return SpatialIndex(revision, rooms, frozenset(storey_names), space_names, storey_names)


class IfcReader:
    """Geometry-oriented IFC reader used by model review and placement flows."""

    def __init__(self, path: Path):
        self.model = ifcopenshell.open(str(path))
        self.length_scale = ifcopenshell.util.unit.calculate_unit_scale(self.model)

    def _get_element(self, global_id: str, ifc_class: str):
        try:
            element = self.model.by_guid(global_id)
        except RuntimeError as error:
            raise ValueError(f"Không tìm thấy GlobalId: {global_id}") from error
        if element is None or not element.is_a(ifc_class):
            raise ValueError(f"GlobalId không thuộc loại {ifc_class}: {global_id}")
        return element

    def get_storeys(self) -> list[dict[str, Any]]:
        storeys = self.model.by_type("IfcBuildingStorey")
        result = [
            {
                "id": storey.GlobalId,
                "name": storey.Name,
                "elevation_m": float(storey.Elevation) * self.length_scale
                if storey.Elevation is not None else None,
            }
            for storey in storeys
        ]
        return sorted(result, key=lambda item: (
            item["elevation_m"] is None, item["elevation_m"] or 0, item["id"]
        ))

    def get_spaces(self, storey_id: str | None = None) -> list[dict[str, Any]]:
        if storey_id is None:
            spaces = self.model.by_type("IfcSpace")
        else:
            storey = self._get_element(storey_id, "IfcBuildingStorey")
            spaces = [
                element for element in ifcopenshell.util.element.get_decomposition(storey)
                if element.is_a("IfcSpace")
            ]
        return sorted([
            {
                "id": space.GlobalId,
                "name": space.Name,
                "long_name": space.LongName,
                "ifc_class": space.is_a(),
            }
            for space in spaces
        ], key=lambda item: item["id"])

    def get_space_geometry(self, space_id: str) -> dict[str, Any]:
        space = self._get_element(space_id, "IfcSpace")
        if space.Representation is None:
            return {"status": "unavailable", "space_id": space_id,
                    "reason": "space_has_no_representation"}
        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        try:
            shape = ifcopenshell.geom.create_shape(settings, space)
            vertices = ifcopenshell.util.shape.get_vertices(shape.geometry)
        except RuntimeError:
            return {"status": "unavailable", "space_id": space_id,
                    "reason": "geometry_generation_failed"}
        if len(vertices) == 0:
            return {"status": "unavailable", "space_id": space_id,
                    "reason": "geometry_has_no_vertices"}
        minimum = vertices.min(axis=0)
        maximum = vertices.max(axis=0)
        return {
            "status": "ok",
            "space_id": space_id,
            "unit": "m",
            "coordinate_system": "model_world",
            "bbox": {"min": minimum.tolist(), "max": maximum.tolist()},
            "bbox_center": ((minimum + maximum) / 2).tolist(),
        }

    def _has_length_unit(self) -> bool:
        for project in self.model.by_type("IfcProject"):
            assignment = getattr(project, "UnitsInContext", None)
            for unit in getattr(assignment, "Units", ()) or ():
                if getattr(unit, "UnitType", None) == "LENGTHUNIT":
                    return True
        return False

    def _element_position(self, element) -> dict[str, float] | None:
        placement = getattr(element, "ObjectPlacement", None)
        if placement is None:
            return None
        try:
            matrix = ifcopenshell.util.placement.get_local_placement(placement)
        except RuntimeError:
            return None
        return {
            axis: float(matrix[index, 3]) * self.length_scale
            for index, axis in enumerate(("x", "y", "z"))
        }

    def _geometry_bbox(self, element) -> dict[str, Any] | None:
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

        # Tọa độ đỉnh hình học của IfcOpenShell đã được đổi sang mét.
        # Chỉ áp dụng length_scale cho ObjectPlacement, không áp dụng cho đỉnh hình học.
        minimum = vertices.min(axis=0)
        maximum = vertices.max(axis=0)
        center = (minimum + maximum) / 2
        return {
            "bbox": {"min": minimum.tolist(), "max": maximum.tolist()},
            "centroid": center.tolist(),
        }

    def _element_record(self, element) -> dict[str, Any]:
        return {
            "element_id": getattr(element, "GlobalId", None),
            "ifc_class": element.is_a(),
            "name": getattr(element, "Name", None),
            "object_type": getattr(element, "ObjectType", None),
            "predefined_type": getattr(element, "PredefinedType", None),
            "position": self._element_position(element),
            "geometry": self._geometry_bbox(element),
            "operation_type": getattr(element, "OperationType", None),
            "opening_direction": None,
        }

    @staticmethod
    def _normalize_label(value: Any) -> str:
        text = unicodedata.normalize("NFKD", str(value or "").casefold())
        return "".join(char for char in text if not unicodedata.combining(char)).replace("đ", "d")

    @classmethod
    def _space_type(cls, name: str | None, long_name: str | None) -> str | None:
        label = cls._normalize_label(f"{long_name or ''} {name or ''}")
        classifications = (
            (("phong ngu", "ngu", "bedroom"), "Phòng ngủ"),
            (("phong khach", "khach", "living"), "Phòng khách"),
            (("bep", "kitchen"), "Bếp"),
            (("phong tam", "wc", "toilet", "bathroom"), "Phòng vệ sinh"),
            (("hanh lang", "corridor", "hallway"), "Hành lang"),
            (("gara", "garage"), "Gara"),
            (("phong lam viec", "study", "office"), "Phòng làm việc"),
        )
        return next((kind for tokens, kind in classifications if any(token in label for token in tokens)), None)

    @classmethod
    def _placement_category(cls, record: dict[str, Any]) -> str | None:
        ifc_class = str(record.get("ifc_class") or "").casefold()
        label = cls._normalize_label(" ".join(str(record.get(key) or "") for key in (
            "name", "object_type", "predefined_type"
        )))
        if "ifcdoor" in ifc_class:
            return "doors"
        if "ifcwindow" in ifc_class:
            return "windows"
        if "ifcwall" in ifc_class or "ifccurtainwall" in ifc_class:
            return "walls"
        if "ifcslab" in ifc_class or "ifccovering" in ifc_class:
            return "ceiling" if "ceiling" in label or "tran" in label else None
        return "obstacles"

    def get_space_context(self, space_id: str) -> dict[str, Any]:
        """Đọc và chuẩn hóa ngữ cảnh bố trí cho một không gian IFC."""
        space = self._get_element(space_id, "IfcSpace")

        boundary_elements = []
        for relation in self.model.by_type("IfcRelSpaceBoundary"):
            relating_space = getattr(relation, "RelatingSpace", None)
            element = getattr(relation, "RelatedBuildingElement", None)
            if (relating_space is not None
                    and relating_space.GlobalId == space_id
                    and element is not None):
                boundary_elements.append(element)

        # Cửa và cửa sổ thường liên kết với lỗ mở trên tường, không liên kết
        # trực tiếp với IfcSpace. Lần theo quan hệ tường -> lỗ mở -> vật thể lấp đầy.
        boundary_host_ids = {
            element.id() for element in boundary_elements
            if getattr(element, "id", None) is not None
        }
        fills_by_opening = {}
        for relation in self.model.by_type("IfcRelFillsElement"):
            opening = getattr(relation, "RelatingOpeningElement", None)
            element = getattr(relation, "RelatedBuildingElement", None)
            if opening is not None and element is not None:
                fills_by_opening.setdefault(opening.id(), []).append(element)
        for relation in self.model.by_type("IfcRelVoidsElement"):
            host = getattr(relation, "RelatingBuildingElement", None)
            opening = getattr(relation, "RelatedOpeningElement", None)
            if host is not None and opening is not None and host.id() in boundary_host_ids:
                boundary_elements.extend(fills_by_opening.get(opening.id(), ()))

        contained_elements = []
        for relation in self.model.by_type("IfcRelContainedInSpatialStructure"):
            structure = getattr(relation, "RelatingStructure", None)
            if structure is not None and structure.GlobalId == space_id:
                contained_elements.extend(getattr(relation, "RelatedElements", ()) or ())

        def unique_records(elements):
            seen = set()
            records = []
            for element in elements:
                element_id = getattr(element, "GlobalId", None)
                if element_id is None or element_id in seen:
                    continue
                seen.add(element_id)
                records.append(self._element_record(element))
            return records

        storey = ifcopenshell.util.element.get_container(space)
        seen_spatial = set()
        while (storey is not None and not storey.is_a("IfcBuildingStorey")
               and storey.id() not in seen_spatial):
            seen_spatial.add(storey.id())
            storey = ifcopenshell.util.element.get_aggregate(storey)

        bounds = self._geometry_bbox(space)
        if bounds is None:
            geometry_data = {"status": "unavailable"}
        else:
            minimum = bounds["bbox"]["min"]
            maximum = bounds["bbox"]["max"]
            geometry_data = {
                "status": "ok",
                "bbox": bounds["bbox"],
                "centroid": bounds["centroid"],
                "area_m2": None,
                "area_bbox_estimate_m2": abs(
                    (maximum[0] - minimum[0]) * (maximum[1] - minimum[1])
                ),
            }
        storey_data = ({
            "id": storey.GlobalId,
            "name": getattr(storey, "Name", None),
            "elevation_m": (
                float(storey.Elevation) * self.length_scale
                if getattr(storey, "Elevation", None) is not None
                else None
            ),
        } if storey is not None else None)
        result: dict[str, Any] = {
            "space_type": self._space_type(
                getattr(space, "Name", None), getattr(space, "LongName", None)
            ),
            "geometry": {
                "room_name": getattr(space, "LongName", None) or getattr(space, "Name", None),
                "bbox": geometry_data.get("bbox"),
                "centroid": geometry_data.get("centroid"),
                "area_m2": geometry_data.get("area_m2"),
                "area_bbox_estimate_m2": geometry_data.get("area_bbox_estimate_m2"),
                "area_bbox_estimate_note": "Ước lượng theo footprint hộp bao; không thay cho diện tích QTO hoặc biên phòng thực.",
                "geometry_quality": "ifc_geometry_bbox" if geometry_data.get("status") == "ok" else "unavailable",
                "storey_name": storey_data.get("name") if storey_data else None,
                "storey_elevation_m": storey_data.get("elevation_m") if storey_data else None,
            },
            "walls": [],
            "ceiling": [],
            "doors": [],
            "windows": [],
            "obstacles": [],
            "coordinate_system": {
                "kind": "Tọa độ dự án IFC",
                "unit": "metre" if self._has_length_unit() else None,
                "scale_applied": self.length_scale,
                "frame": "Hệ tọa độ cục bộ của dự án IFC; chưa chuyển đổi sang tọa độ địa lý",
                "elevation_m": (
                    float(storey.Elevation) * self.length_scale
                    if storey is not None and getattr(storey, "Elevation", None) is not None
                    else None
                ),
            },
            "missing_data": [],
        }

        seen = set()
        for source, elements in (("boundary", boundary_elements), ("contained", contained_elements)):
            for record in unique_records(elements):
                element_id = record["element_id"]
                if element_id in seen:
                    continue
                seen.add(element_id)
                category = self._placement_category(record)
                if category is None:
                    continue
                if source == "contained" and category not in ("doors", "windows"):
                    category = "obstacles"
                result[category].append(record)

        missing = result["missing_data"]
        if result["space_type"] is None:
            missing.append("space_type")
        if geometry_data.get("status") != "ok":
            missing.append("geometry")
        if geometry_data.get("area_m2") is None:
            missing.append("area_m2")
        missing.append("room_polygon_2d")
        if storey_data is None:
            missing.append("storey")
        elif storey_data.get("elevation_m") is None:
            missing.append("storey_elevation")
        for field in ("walls", "ceiling", "doors", "windows", "obstacles"):
            if not result[field]:
                missing.append(field)
            elif any(record.get("geometry") is None for record in result[field]):
                missing.append(f"{field}_geometry")
        if result["doors"] and any(not door.get("opening_direction") for door in result["doors"]):
            missing.append("door_opening_direction")
        if result["coordinate_system"].get("unit") is None:
            missing.append("coordinate_system")
        return result


class PlaybookReader:
    """Đọc dữ liệu tra cứu Playbook từ Supabase đã cấu hình."""

    def __init__(self, client=None):
        if client is None:
            from config.settings import Settings
            try:
                from database.supabase_client import get_supabase_client
            except ImportError as error:
                raise RuntimeError(
                    "Không import được Supabase SDK hoặc dependency đi kèm "
                    f"({error}). Hãy kiểm tra/cập nhật supabase và websockets "
                    "trong đúng môi trường Python đang chạy test."
                ) from error

            client = get_supabase_client(Settings())
        self.client = client

    @staticmethod
    def _normalize(value: Any) -> str:
        text = unicodedata.normalize("NFKD", str(value or "").casefold())
        return "".join(char for char in text if not unicodedata.combining(char)).replace("đ", "d")

    def _rows(self, table: str) -> list[dict[str, Any]]:
        try:
            response = self.client.table(table).select("*").execute()
        except Exception as error:
            if "Invalid API key" in str(error):
                raise ValueError(
                    "Supabase từ chối SUPABASE_KEY. Hãy thay bằng API key của "
                    "đúng project; với backend dùng secret/service_role key. "
                    "Không dùng mật khẩu PostgreSQL hoặc DATABASE_URL làm API key."
                ) from error
            raise
        return response.data or []

    @classmethod
    def _room_applies(cls, scope: Any, space_type: str) -> bool:
        normalized_scope = cls._normalize(scope)
        normalized_room = cls._normalize(space_type)
        if not normalized_scope or not normalized_room:
            return False
        if "cac phong" in normalized_scope or "cac khong gian" in normalized_scope:
            return True
        scopes = [part.strip() for part in normalized_scope.replace(",", ";").split(";")]
        return any(
            room and (room in normalized_room or normalized_room in room)
            for room in scopes
        )

    @classmethod
    def _device_applies(cls, device_type: str, *values: Any) -> bool:
        query = cls._normalize(device_type)
        if not query:
            return False
        text = " ".join(cls._normalize(value) for value in values if value is not None)
        text = text.replace(",", " ").replace(";", " ").replace("/", " ")
        if query in text or any(cls._normalize(value) in query for value in values if value):
            return True
        ignored = {"diem", "cap", "dien", "thiet bi", "phong", "cho"}
        query_words = {word for word in query.split() if len(word) > 2 and word not in ignored}
        text_words = {word for word in text.split() if len(word) > 2 and word not in ignored}
        return bool(query_words & text_words)

    def get_object_rules(
        self, device_type: str, space_type: str, project_standard: str
    ) -> dict[str, Any]:
        rules = []
        for row in self._rows("quy_tac_doi_tuong"):
            keywords = row.get("tu_khoa_nhan_dien") or []
            if isinstance(keywords, str):
                keywords = keywords.replace(",", ";").split(";")
            if not self._device_applies(
                device_type, row.get("ten_quy_tac"), *keywords
            ) or not self._room_applies(row.get("khong_gian_thuong_gap"), space_type):
                continue
            rules.append({
                "rule_id": row.get("ma_quy_tac"),
                "quantity_rule": None,
                "spacing": None,
                "mounting_height": None,
                "host_type": None,
                "constraints": [
                    value for value in (
                        row.get("dieu_kien_bo_sung"), row.get("xu_ly_canh_bao")
                    ) if value
                ],
                "source": "Playbook > Quy tắc đối tượng",
                "source_status": row.get("tinh_trang_kiem_chung"),
                "scope": row.get("khong_gian_thuong_gap"),
                "standard_applicability": "Chưa được nêu trong nguồn",
                "raw_rule": row,
            })

        for row in self._rows("quy_tac_phong"):
            if not self._room_applies(row.get("loai_khong_gian"), space_type):
                continue
            if not self._device_applies(
                device_type, row.get("thiet_bi_thuong_xet")
            ):
                continue
            rules.append({
                "rule_id": f"room:{row.get('id')}",
                "quantity_rule": None,
                "spacing": None,
                "mounting_height": row.get("cao_do_lap_dat"),
                "host_type": None,
                "constraints": [
                    value for value in (
                        row.get("nguyen_tac_bo_tri"), row.get("kiem_tra_bo_sung"),
                        row.get("gioi_han_canh_bao"),
                    ) if value
                ],
                "source": "Playbook > Quy tắc phòng",
                "source_status": row.get("loai_du_lieu"),
                "scope": row.get("loai_khong_gian"),
                "standard_applicability": "Chưa được nêu trong nguồn",
                "raw_rule": row,
            })
        return {"rules": rules}

    @staticmethod
    def _filter_row(row: dict[str, Any], filters: dict[str, Any] | None) -> bool:
        for key, expected in (filters or {}).items():
            if expected is None or expected == "":
                continue
            if key not in row:
                return False
            actual = row[key]
            if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
                if actual != expected:
                    return False
            elif PlaybookReader._normalize(expected) not in PlaybookReader._normalize(actual):
                return False
        return True

    def query_devices(
        self, category: str, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        # Workbook hiện chỉ có mô đun đóng cắt, thiết bị đóng cắt và đầu nối;
        # chưa có thư viện IFC hoặc hình học cho đèn và ổ cắm.
        sources = (
            ("mo_dun_thiet_bi", "ten_cau_hinh"),
            ("thiet_bi_dong_cat", "ma_cau_hinh_mo_ta"),
            ("dau_noi_dien", "ten_dau_noi"),
        )
        query = self._normalize(category)
        result = []
        for table, key in sources:
            for row in self._rows(table):
                if not self._device_applies(query, *row.values()):
                    continue
                if not self._filter_row(row, filters):
                    continue
                result.append({
                    "module_id": row.get(key),
                    "name": row.get(key),
                    "ifc_class": row.get("ifc_class"),
                    "specs": row,
                    "rated_power": row.get("rated_power_w"),
                    "geometry_ref": row.get("geometry_ref"),
                })
        return result

    def get_load_class(self, space_type: str, device_category: str) -> dict[str, Any]:
        category = self._normalize(device_category)
        aliases = (
            (("den", "lighting"), "chieu sang"),
            (("o cam", "socket", "outlet"), "o cam"),
            (("dieu hoa", "air conditioner", "ac"), "dieu hoa"),
            (("binh nong lanh", "water heater"), "binh nuoc nong"),
            (("may bom", "pump"), "may bom"),
        )
        category = next((target for keys, target in aliases if any(
            category == key or category.startswith(f"{key} ") for key in keys
        )), category)
        room = self._normalize(space_type)
        candidates = []
        for row in self._rows("lop_phu_tai"):
            searchable = self._normalize(
                f"{row.get('ten_lop_phu_tai', '')} {row.get('nhom_phu_tai', '')}"
            )
            if not category or category not in searchable:
                continue
            score = 1
            if room and room in searchable:
                score += 2
            candidates.append((score, row))

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

    @staticmethod
    def _supported_phases(power_supply: Any) -> list[str]:
        text = PlaybookReader._normalize(power_supply)
        phases = []
        if "mot pha" in text:
            phases.append("1 pha")
        if "ba pha" in text:
            phases.append("3 pha")
        return phases

    def get_terminal_class(
        self, device_type: str, connection_requirements: Any
    ) -> dict[str, Any]:
        requirements = (
            connection_requirements
            if isinstance(connection_requirements, dict)
            else {"description": connection_requirements}
        )
        loads = {
            row.get("ma_lop_phu_tai"): row
            for row in self._rows("lop_phu_tai")
        }
        candidates = []
        for row in self._rows("lop_dau_cuoi"):
            load = loads.get(row.get("lop_phu_tai_ma"), {})
            if not self._device_applies(
                device_type, row.get("ten_lop_dau_cuoi"), load.get("ten_lop_phu_tai")
            ):
                continue
            phases = self._supported_phases(row.get("kieu_cap_dien"))
            requested_phases = requirements.get("phases")
            if requested_phases:
                requested = requested_phases if isinstance(requested_phases, list) else [requested_phases]
                requested_norm = set()
                for value in requested:
                    value_text = self._normalize(value)
                    if value == 1 or "1 pha" in value_text or "mot pha" in value_text:
                        requested_norm.add("1 pha")
                    elif value == 3 or "3 pha" in value_text or "ba pha" in value_text:
                        requested_norm.add("3 pha")
                    else:
                        requested_norm.add(value_text)
                if not requested_norm.issubset({self._normalize(value) for value in phases}):
                    continue
            requested_connection = requirements.get("connection_type")
            if requested_connection and self._normalize(requested_connection) not in self._normalize(row.get("kieu_diem_ket_noi")):
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
        return {
            "terminal_class_id": row.get("ma_lop_dau_cuoi"),
            "voltage": None,
            "phases": phases,
            "connection_specs": {
                "power_supply_type": row.get("kieu_cap_dien"),
                "connection_type": row.get("kieu_diem_ket_noi"),
                "circuit_group": row.get("nhom_mach"),
                "applicable_space": row.get("khong_gian_ap_dung"),
                "voltage_drop_limit_percent": row.get("gioi_han_sut_ap_pct"),
                "notes": row.get("ghi_chu"),
                "source_status": row.get("tinh_trang_kiem_chung"),
                "missing_data": ["voltage"] if row.get("dien_ap_v") is None else [],
            },
        }
