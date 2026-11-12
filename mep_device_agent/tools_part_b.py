import math
import os
from typing import Dict, List, Any, Optional
from pathlib import Path

from core.ifc_reader import IFCReader
from core.placement_engine import PlacementEngine
from db.database import get_db_connection

DEFAULT_IFC_PATH = str(Path(__file__).resolve().parent / "data" / "ctr.ifc")

# =====================================================================
# B. Sinh phương án và kiểm tra (3 Tools cốt lõi)
# =====================================================================

def ifc_list_placed_devices(
    space_id: Optional[str] = None,
    storey_id: Optional[str] = None,
    ifc_file_path: Optional[str] = None,
    ifc_type: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    [Tool 7: ifc_list_placed_devices]
    
    1. MỤC ĐÍCH:
       Truy vấn và trích xuất toàn bộ danh sách thiết bị MEP (đèn, ổ cắm, công tắc, thiết bị vệ sinh),
       thiết bị gia dụng và đồ nội thất đã tồn tại sẵn trong mô hình kiến trúc IFC hoặc bản nháp thiết kế.

    2. KHI NÀO DÙNG (WHEN TO USE):
       - Gọi BẮT BUỘC ở bước khởi đầu trước khi Engine sinh phương án (Tool 8), nhằm mục đích:
         + Tránh đặt trùng đè thiết bị mới lên thiết bị cũ (Collision Avoidance).
         + Phát hiện các thiết bị nội thất quan trọng (giường, sofa, bàn làm việc, tủ bếp) để định vị ổ cắm.
         + Hỗ trợ kỹ sư so sánh đối chiếu giữa hiện trạng và phương án thiết kế mới.
       - Gọi khi Kỹ sư yêu cầu kiểm tra hiện trạng thiết bị trong một phòng cụ thể (VD: "Phòng khách đã có đèn chưa?").

    3. THAM SỐ ĐẦU VÀO (PARAMETERS):
       - space_id (Optional[str]): Mã định danh hoặc tên của phòng/không gian cần lọc (VD: '12', 'Phòng khách', 'space_01').
                                   Nếu là None, hàm sẽ quét trên toàn bộ phạm vi tòa nhà.
       - storey_id (Optional[str]): Mã định danh tầng cần lọc (VD: 'Storey 1', 'Tầng 1'). Mặc định là None.
       - ifc_file_path (Optional[str]): Đường dẫn tuyệt đối tới file IFC cần đọc. Nếu None sẽ dùng file mặc định.
       - ifc_type (Optional[List[str]]): Danh sách các IFC Entity Type cần truy vấn. Mặc định quét 4 nhóm chính:
           + 'IfcFlowTerminal': Thiết bị đầu cuối MEP (đèn chiếu sáng, ổ cắm, thiết bị vệ sinh lavabo, bồn cầu).
           + 'IfcBuildingElementProxy': Thiết bị điện tử/cơ điện do KTS vẽ dưới dạng mô hình chung (Generic Models).
           + 'IfcFurniture': Đồ nội thất (giường ngủ, bàn ăn, sofa, bàn làm việc).
           + 'IfcSystemFurnitureElement': Module tủ bếp, bàn cụm văn phòng.

    4. KẾT QUẢ TRẢ VỀ (OUTPUT SCHEMA):
       Trả về danh sách các đối tượng thiết bị, mỗi phần tử gồm:
       [
         {
           "element_id": str,          # GlobalId duy nhất trong file IFC (VD: '3TWm_gz$L7HPCl3COa3rlK')
           "device_type": str,         # Tên loại thiết bị hoặc Family Type (VD: 'socket_standard', 'Lavabo', 'Sofa')
           "space_id": str,            # Tên/Mã phòng chứa thiết bị
           "position": {               # Tọa độ thực 3D (đơn vị: mét)
             "x": float,
             "y": float,
             "z": float
           },
           "properties": {             # Thuộc tính kỹ thuật
             "category": str,          # Phân loại ('electrical', 'plumbing', 'furniture')
             "load_w": float,          # Công suất điện (Watt) nếu có
             "host_type": str,         # Bề mặt gắn ('wall', 'floor', 'ceiling')
             "ifc_type": str           # Lớp IFC gốc ('IfcFlowTerminal', 'IfcFurniture'...)
           }
         },
         ...
       ]
    """
    path = ifc_file_path or DEFAULT_IFC_PATH
    reader = IFCReader(path)
    return reader.get_existing_placed_devices(storey_id=storey_id, space_id=space_id, ifc_type=ifc_type)


# Tool 9: engine_validate_placements is defined in core/placement_engine.py and is called from agent/agent_part_b.py. It validates proposed device placements against the IFC model, checking for bounds, host compatibility, collisions, duplication, and Z-level standards. It returns a dictionary with validation results, including errors, warnings, and suggested fixes.

def engine_validate_placements(
    proposals: List[Dict[str, Any]],
    placement_context: Dict[str, Any],
    rules: Optional[Dict[str, Any]] = None,
    ifc_file_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Tool 9: engine_validate_placements (Mới – Quan trọng)
    Kiểm tra phương án trước khi lưu chính thức, bao gồm cả vị trí Agent tự sinh
    và vị trí do kỹ sư chỉnh sửa. Kiểm tra thiết bị có nằm đúng phòng, đúng bề mặt,
    có đè lên cửa, cửa sổ, cột hoặc vật cản, có trùng thiết bị khác, và có phù hợp
    các quy tắc đã chọn hay không. Phân biệt lỗi bắt buộc sửa và cảnh báo cần kỹ sư đánh giá.

    Input:
      proposals: Danh sách các vị trí đề xuất cần kiểm tra
      placement_context: Context (standard_profile, v.v.)
      rules: Bộ quy tắc
      ifc_file_path: Đường dẫn file IFC để đối chiếu hình học

    Output:
      {
        valid: bool,
        errors: [{code, severity: "CRITICAL", device_id, message}],
        warnings: [{code, severity: "WARNING", device_id, message}],
        suggested_fixes: [{device_id, action, suggested_position, explanation}]
      }
    """
    path = ifc_file_path or DEFAULT_IFC_PATH
    reader = IFCReader(path)
    
    spaces_list = reader.get_spaces()
    spaces_dict = {s["id"]: s for s in spaces_list}
    # Also index by code/name to support flexible references
    for s in spaces_list:
        spaces_dict[s["code"]] = s
        spaces_dict[s["name"]] = s

    doors = reader.get_doors_and_windows()

    validation_result = PlacementEngine.validate_placements(
        proposals=proposals,
        placement_context=placement_context or {"standard_profile": "office"},
        rules=rules or {},
        spaces_dict=spaces_dict,
        doors=doors
    )

    return validation_result
