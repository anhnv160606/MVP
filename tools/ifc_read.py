"""Tool 7: liệt kê thiết bị đã có trong mô hình IFC."""

import os
from pathlib import Path
from typing import Any

import ifcopenshell.util.element
import ifcopenshell.util.placement

from ifc_engine.reader import IfcReader, PlaybookReader


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
    """Lấy thiết bị theo GlobalId phòng/tầng, dùng quan hệ không gian của IFC.

    Tọa độ là gốc ObjectPlacement trong hệ tọa độ model, đơn vị mét; None
    nếu không có placement. Không suy đoán phòng từ bounding box. Khi không
    lọc phòng, thiết bị không có quan hệ IfcSpace được trả về với space_id=None.
    ifc_types cho phép giới hạn các lớp IFC, mặc định gồm thiết bị MEP, proxy
    và nội thất để phục vụ tránh trùng và bố trí theo nội thất.
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


def _default_ifc_path() -> Path:
    """Tìm đường dẫn mô hình mặc định mà không thêm tham số cho tool."""
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
        "Không tìm thấy IFC. Đặt IFC_FILE_PATH hoặc thêm file vào ifc_file/."
    )


def ifc_get_placement_context(space_id: str) -> dict[str, Any]:
    """Tool 1: chỉ nhận space_id; IFC được đọc và chuẩn hóa bởi IfcReader."""
    return IfcReader(_default_ifc_path()).get_space_context(space_id)


def playbook_get_object_rules(
    device_type: str,
    space_type: str,
    project_standard: str,
) -> dict[str, Any]:
    """Tool 3: tra quy tắc đối tượng và quy tắc phòng từ Playbook."""
    return PlaybookReader().get_object_rules(device_type, space_type, project_standard)


def playbook_query_devices(
    category: str,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Tool 4: tìm thiết bị có thật trong các catalog Playbook."""
    return PlaybookReader().query_devices(category, filters)


def playbook_get_load_class(
    space_type: str,
    device_category: str,
) -> dict[str, Any]:
    """Tool 5: tra lớp phụ tải; không trả hệ số nhu cầu như thể là loại tải."""
    return PlaybookReader().get_load_class(space_type, device_category)


def playbook_get_terminal_class(
    device_type: str,
    connection_requirements: Any,
) -> dict[str, Any]:
    """Tool 6: tra lớp đầu cuối và thông tin kết nối hiện có trong Playbook."""
    return PlaybookReader().get_terminal_class(device_type, connection_requirements)
