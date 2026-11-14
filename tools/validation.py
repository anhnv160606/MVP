"""Tool 9: kiểm tra bước đầu proposals JSON dựa trên mô hình IFC.

Kiểm tra hình học dùng bounding box và điểm gốc thiết bị, do đó luôn cần
kiểm tra tiếp thể tích phòng, bề mặt gắn và kích thước thiết bị thực tế.
"""

import argparse
import json
import math
from pathlib import Path
from typing import Any

import ifcopenshell.geom
import ifcopenshell.util.shape

from ifc_engine.reader import IfcReader
from tools.ifc_read import ifc_list_placed_devices


def engine_validate_placements(
    proposals: list[dict[str, Any]],
    placement_context: dict[str, Any],
    rules: dict[str, Any] | None = None,
    ifc_file_path: str | Path | None = None,
) -> dict[str, Any]:
    """Kiểm tra bước đầu các vị trí do AI hoặc kỹ sư đề xuất trước khi lưu IFC.

    Gọi sau khi có proposals và context của cùng model IFC. Tool kiểm tra
    cấu trúc, ID, bounding box phòng/host, gần trùng vị trí và khả năng va
    chạm với bounding box cửa, cửa sổ, cột, nội thất và proxy. Đây là kiểm
    tra gần đúng bằng điểm gốc, không phải kiểm tra va chạm hình học đầy đủ.

    Args:
        proposals: Danh sách object. Mỗi object cần proposed_id, device_type,
            space_id (GlobalId IfcSpace), host_id (GlobalId IfcProduct) và
            position={x, y, z} là số hữu hạn tính bằng mét trong hệ tọa độ
            model. orientation={x, y, z} thiếu/sai sẽ cảnh báo; hướng vẫn
            chưa được kiểm định. rule_ids nếu có sẽ được báo chưa kiểm tra.
        placement_context: Object; có thể chứa existing_devices từ Tool 7.
            Nếu thiếu hoặc None, tool tự gọi Tool 7 cho toàn bộ model. Có thể
            chứa ifc_file_path nếu không truyền tham số cùng tên riêng.
        rules: Object tùy chọn; chỉ hỗ trợ min_separation_m là số không âm,
            dùng ngưỡng khoảng cách 3D để cảnh báo gần trùng. Rule khác được
            báo là chưa kiểm tra. Khi không có ngưỡng, chỉ cảnh báo khoảng cách
            dưới 0.001 m; không mặc định đó là tiêu chuẩn thiết kế.
        ifc_file_path: Đường dẫn IFC; ưu tiên hơn đường dẫn trong context.
            Bắt buộc cung cấp ở một trong hai nơi.

    Returns:
        Object gồm valid (True khi không phát hiện errors), complete (hiện
        luôn False), errors và warnings (danh sách {code, device_id, message}),
        suggested_fixes (hiện luôn []). valid=True không xác nhận thiết kế an
        toàn hoặc tuân thủ Playbook; đọc warnings và yêu cầu kỹ sư kiểm tra.

    Raises:
        ValueError: proposals/context/rules sai kiểu, thiếu đường dẫn IFC hoặc
            min_separation_m không hợp lệ. Lỗi đọc file/model có thể truyền ra.

    Giới hạn: chưa kiểm tra chính xác thể tích phòng, bề mặt và pháp tuyến
    host, kích thước thiết bị, vùng mở cửa hoặc nội dung rule Playbook; không
    tự sửa proposals, ghi IFC hay phê duyệt thiết kế.
    """
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    fixes: list[dict[str, Any]] = []

    def issue(target, code, message, device_id=None):
        target.append({"code": code, "device_id": device_id, "message": message})

    if not isinstance(proposals, list):
        raise ValueError("proposals phải là một danh sách")
    if not proposals:
        issue(errors, "ERR_EMPTY_PROPOSALS", "Danh sách proposals đang rỗng.")
    if not isinstance(placement_context, dict):
        raise ValueError("placement_context phải là một object")
    if rules is not None and not isinstance(rules, dict):
        raise ValueError("rules phải là một object")
    rules = rules or {}
    path = ifc_file_path or placement_context.get("ifc_file_path")
    if not path:
        raise ValueError("Cần ifc_file_path để kiểm tra model IFC")
    reader = IfcReader(Path(path))
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    geometry_cache: dict[str, Any] = {}

    def geometry(entity):
        if entity.GlobalId not in geometry_cache:
            try:
                shape = ifcopenshell.geom.create_shape(settings, entity)
                vertices = ifcopenshell.util.shape.get_vertices(shape.geometry)
                geometry_cache[entity.GlobalId] = (
                    (vertices.min(axis=0), vertices.max(axis=0)) if len(vertices) else None
                )
            except (RuntimeError, ValueError, AttributeError):
                geometry_cache[entity.GlobalId] = None
        return geometry_cache[entity.GlobalId]

    def point_in_box(point, box, tolerance=0.0):
        lower, upper = box
        return all(float(lower[i]) - tolerance <= point[i] <= float(upper[i]) + tolerance for i in range(3))

    def finite_number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    separation = rules.get("min_separation_m")
    if separation is not None and (not finite_number(separation) or separation < 0):
        raise ValueError("rules.min_separation_m phải là số không âm")
    for name in rules:
        if name != "min_separation_m":
            issue(warnings, "WARN_RULE_NOT_CHECKED", f"Chưa hỗ trợ kiểm tra rule '{name}'.")
    issue(warnings, "WARN_APPROXIMATE_GEOMETRY", "Kiểm tra dùng bounding box và điểm gốc; kỹ sư cần kiểm tra hình dạng phòng, bề mặt, vật cản và kích thước thiết bị.")

    existing = placement_context.get("existing_devices")
    if existing is None:
        existing = ifc_list_placed_devices(path)
    if not isinstance(existing, list):
        raise ValueError("placement_context.existing_devices phải là một danh sách")
    missing_positions = sum(
        1 for device in existing
        if not isinstance(device, dict) or not isinstance(device.get("position"), dict)
        or any(not finite_number(device["position"].get(a)) for a in ("x", "y", "z"))
    )
    if missing_positions:
        issue(warnings, "WARN_EXISTING_POSITION_UNAVAILABLE", f"{missing_positions} thiết bị hiện có thiếu tọa độ; chưa kiểm tra được khoảng cách.")

    obstacles = []
    for ifc_class in ("IfcDoor", "IfcWindow", "IfcColumn", "IfcFurniture", "IfcBuildingElementProxy"):
        try:
            obstacles.extend(reader.model.by_type(ifc_class))
        except RuntimeError:
            continue
    valid_positions = []
    used_ids = set()
    missing_obstacle_geometry = set()
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            issue(errors, "ERR_INVALID_PROPOSAL", f"Proposal tại index {index} phải là object.")
            continue
        device_id = proposal.get("proposed_id")
        if not isinstance(device_id, str) or not device_id.strip():
            issue(errors, "ERR_MISSING_ID", f"Proposal tại index {index} thiếu proposed_id.")
            continue
        if device_id in used_ids:
            issue(errors, "ERR_DUPLICATE_ID", "proposed_id bị trùng.", device_id)
        used_ids.add(device_id)
        for field in ("device_type", "space_id", "host_id"):
            if not isinstance(proposal.get(field), str) or not proposal[field].strip():
                issue(errors, "ERR_MISSING_FIELD", f"Thiếu hoặc sai trường {field}.", device_id)
        pos = proposal.get("position")
        if not isinstance(pos, dict) or any(not finite_number(pos.get(axis)) for axis in ("x", "y", "z")):
            issue(errors, "ERR_INVALID_POSITION", "position cần x, y, z là số hữu hạn (m).", device_id)
            continue
        point = tuple(pos[axis] for axis in ("x", "y", "z"))
        valid_positions.append((device_id, point))
        orientation = proposal.get("orientation")
        if proposal.get("rule_ids"):
            issue(warnings, "WARN_RULE_IDS_UNCHECKED", "Chưa đối chiếu rule_ids của proposal với Playbook.", device_id)
        if not isinstance(orientation, dict) or any(not finite_number(orientation.get(axis)) for axis in ("x", "y", "z")):
            issue(warnings, "WARN_ORIENTATION_UNCHECKED", "Thiếu orientation x, y, z hợp lệ; chưa thể kiểm tra hướng lắp.", device_id)
        else:
            issue(warnings, "WARN_ORIENTATION_UNCHECKED", "Chưa kiểm tra hướng xoay với pháp tuyến bề mặt.", device_id)
        try:
            space = reader._get_element(proposal.get("space_id", ""), "IfcSpace")
        except (ValueError, TypeError):
            issue(errors, "ERR_UNKNOWN_SPACE", "space_id không phải GlobalId của IfcSpace.", device_id)
        else:
            box = geometry(space)
            if box is None:
                issue(warnings, "WARN_SPACE_GEOMETRY_UNAVAILABLE", "Không lấy được hình học phòng.", device_id)
            elif not point_in_box(point, box, tolerance=0.02):
                issue(errors, "ERR_OUTSIDE_SPACE_BBOX", "Điểm nằm ngoài bounding box phòng (dung sai 0.02 m).", device_id)
        host_id = proposal.get("host_id")
        try:
            host = reader.model.by_guid(host_id) if isinstance(host_id, str) else None
        except RuntimeError:
            host = None
        if host is None or not host.is_a("IfcProduct"):
            issue(errors, "ERR_UNKNOWN_HOST", "host_id không phải GlobalId của IfcProduct.", device_id)
        else:
            if not any(host.is_a(c) for c in ("IfcWall", "IfcSlab", "IfcCovering", "IfcRoof", "IfcCurtainWall", "IfcBuildingElementProxy")):
                issue(warnings, "WARN_HOST_TYPE_UNCHECKED", f"Host {host.is_a()} cần được kỹ sư xác nhận là bề mặt lắp phù hợp.", device_id)
            host_box = geometry(host)
            if host_box is None:
                issue(warnings, "WARN_HOST_GEOMETRY_UNAVAILABLE", "Không lấy được hình học host.", device_id)
            elif not point_in_box(point, host_box, tolerance=0.1):
                issue(warnings, "WARN_FAR_FROM_HOST", "Điểm nằm ngoài bounding box host hơn 0.1 m; cần kiểm tra bề mặt.", device_id)
        for obstacle in obstacles:
            if obstacle.GlobalId == host_id:
                continue
            box = geometry(obstacle)
            if box is None:
                missing_obstacle_geometry.add(obstacle.GlobalId)
            elif point_in_box(point, box):
                issue(warnings, "WARN_POSSIBLE_OBSTACLE_COLLISION", f"Điểm nằm trong bounding box {obstacle.is_a()} {obstacle.GlobalId}; cần kiểm tra hình học thật.", device_id)
        for device in existing:
            epos = device.get("position") if isinstance(device, dict) else None
            if not isinstance(epos, dict) or any(not finite_number(epos.get(a)) for a in ("x", "y", "z")):
                continue
            distance = math.dist(point, tuple(epos[a] for a in ("x", "y", "z")))
            if distance < 0.001 or (separation is not None and distance < separation):
                issue(warnings, "WARN_EXISTING_DEVICE_PROXIMITY", f"Gần thiết bị hiện có {device.get('element_id')} ({distance:.3f} m); cần kiểm tra.", device_id)

    if missing_obstacle_geometry:
        issue(warnings, "WARN_OBSTACLE_GEOMETRY_UNAVAILABLE", f"Không lấy được hình học của {len(missing_obstacle_geometry)} cửa/cột/nội thất/vật cản.")
    for i, (first_id, first_point) in enumerate(valid_positions):
        for second_id, second_point in valid_positions[i + 1:]:
            distance = math.dist(first_point, second_point)
            if distance < 0.001 or (separation is not None and distance < separation):
                issue(warnings, "WARN_PROPOSAL_PROXIMITY", f"Gần proposal {second_id} ({distance:.3f} m); cần kiểm tra.", first_id)
    return {"valid": not errors, "complete": False, "errors": errors, "warnings": warnings, "suggested_fixes": fixes}


def validate_proposals_file(proposals_file: str | Path, ifc_file_path: str | Path, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """Đọc file JSON proposals rồi gọi engine_validate_placements.

    Args:
        proposals_file: JSON UTF-8 chứa {"proposals": [...]} hoặc trực tiếp
            một mảng proposals theo schema của engine_validate_placements.
        ifc_file_path: Đường dẫn file IFC dùng đối chiếu.
        rules: Object quy tắc tùy chọn, ví dụ {"min_separation_m": 0.25}.

    Returns:
        Kết quả kiểm định của engine_validate_placements. Hàm tự lấy thiết bị
        hiện có từ IFC. Lỗi đọc JSON/IFC hoặc đầu vào không hợp lệ được truyền ra.
    """
    with Path(proposals_file).open(encoding="utf-8") as stream:
        data = json.load(stream)
    proposals = data.get("proposals") if isinstance(data, dict) else data
    return engine_validate_placements(proposals, {}, rules, ifc_file_path)


def main():
    parser = argparse.ArgumentParser(description="Kiểm tra proposals JSON bằng Tool 9")
    parser.add_argument("--ifc", required=True, help="Đường dẫn file IFC")
    parser.add_argument("--proposals", required=True, help="Đường dẫn file JSON của AI")
    parser.add_argument("--rules", help="JSON object chứa rules, ví dụ min_separation_m")
    args = parser.parse_args()
    rules = json.loads(Path(args.rules).read_text(encoding="utf-8")) if args.rules else None
    print(json.dumps(validate_proposals_file(args.proposals, args.ifc, rules), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
