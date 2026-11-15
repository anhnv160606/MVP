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
