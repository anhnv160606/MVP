# Phase 2 — Chỉnh sửa thiết bị

`ifc_modify_device(device_id, changes, ...)` chỉnh sửa một thiết bị trong một phương án đã lưu. Tool hỗ trợ dời vị trí, xoay, đổi phòng hoặc host, đổi loại, sửa thông tin catalog và cập nhật thuộc tính. Backend truyền `project_id`, `plan_id`, `expected_version`, `validate_placements`, `placement_context` và `rules` theo phạm vi dự án đang mở.

## Nguyên lý

1. Khi `target="draft"`, `device_id` là `proposed_id`. Khi `target="ifc"`, mã là GlobalId của thiết bị được tạo qua luồng áp dụng Phase 2. Tool đối chiếu GlobalId với phương án đã áp dụng và với IFC hiện tại, rồi tìm thiết bị tương ứng trong bản nháp mới nhất. Các chỉnh sửa chờ duyệt của thiết bị khác vẫn được đưa vào phương án.
2. Tool tạo bản sao phương án, ghép các trường thay đổi vào thiết bị và kiểm tra định dạng theo hợp đồng IFC của Phase 2. `properties` ghép theo khóa cấp đầu, ví dụ sửa `label` vẫn giữ `tag`. `host_id: None` bỏ gắn host. Khi đổi phòng, host và mã quy tắc cũ được bỏ nếu không có giá trị thay thế. Khi đổi loại hoặc lớp IFC, các trường catalog và mã quy tắc cũ được bỏ theo cùng cách.
3. Tool gọi `engine_validate_placements` qua tham số `validate_placements` với **toàn bộ danh sách thiết bị đã chỉnh** cùng context và rules. Validator trả `valid`, `errors`, `warnings` và `suggested_fixes`. Thiếu validator, validator lỗi hoặc trả dữ liệu sai định dạng sẽ dừng lượt sửa.
4. Kết quả hợp lệ tạo một bản `pending_review`, có `updated_device`, `validation_result`, `plan_id` và `version`. Nếu phiên bản gốc đã cũ, tool báo `conflict`. Nếu dữ liệu giống bản cũ, tool báo `unchanged`. Kết quả `validation_failed` giữ nguyên phản hồi validator để kỹ sư biết cần sửa gì. Model IFC chỉ thay đổi sau khi backend ghi quyết định duyệt và Orchestrator gọi `apply_approved_placement_plan`.

Tool xử lý các thiết bị thuộc phương án do Phase 2 quản lý. Khi ghép vào API, backend chịu trách nhiệm xác thực người dùng, chọn context và rules đúng cho phương án, đồng thời nối validator thật. Repo hiện chưa có `engine_validate_placements`. Validator bên dưới chỉ dùng để thử luồng sửa với một quy tắc đơn giản về trùng tọa độ, chưa kiểm tra va chạm, host hay tiêu chuẩn điện.

## Kiểm tra thủ công trong VS Code với A.ifc

Mở thư mục `D:\AI_CONSTRUCTTION\MVP` trên nhánh `phase-2/ifc-modify-device`. Tạo file tạm `manual_modify_a.py` ở thư mục gốc MVP và dán đoạn Python bên dưới. Chạy hai dòng sau trong Terminal PowerShell. Có thể bỏ qua dòng cài thư viện nếu môi trường đã chạy bài thử IFC trước.

```powershell
py -m pip install -r requirements-ifc.txt
py -u .\manual_modify_a.py
```

```python
import hashlib
import os
import tempfile
from pathlib import Path

from ifcopenshell.util.element import get_container
from ifcopenshell.util.placement import get_local_placement

from agent.orchestrator import apply_approved_placement_plan
from database.placement_application import approve_placement_plan, get_project_ifc_path, register_project_ifc
from database.project_store import ProjectStore
from ifc_engine.writer import open_model
from tools.draft_save_placements import draft_save_placements
from tools.ifc_modify_device import ifc_modify_device

source = Path(r"D:\AI_CONSTRUCTTION\A.ifc")
original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
test_dir = Path(tempfile.mkdtemp(prefix="mvp-modify-a-"))
os.environ["MVP_PROJECT_DB_PATH"] = str(test_dir / "project.db")
project = "manual-modify"
space = "2cpX_k3PjBxxi1pGo0wUjc"  # Phòng khách tầng 1

def demo_validator(*, proposals, placement_context, rules):
    positions = {(item["space_id"], tuple(item["position"])) for item in proposals}
    errors = [] if len(positions) == len(proposals) else [{"code": "duplicate_position"}]
    return {"valid": not errors, "errors": errors, "warnings": [], "suggested_fixes": []}

print("Đăng ký bản sao IFC...")
register_project_ifc(project, source)
a = {"proposed_id": "A", "device_type": "light", "space_id": space, "position": [-7.5, -3.5, 2.7]}
b = {**a, "proposed_id": "B", "position": [-6.5, -3.5, 2.7]}
context = {"space_id": space}
rules = ["duplicate_position"]
initial = demo_validator(proposals=[a, b], placement_context=context, rules=rules)
draft = draft_save_placements(project, [a, b], initial)
plan = draft["plan_id"]
opts = dict(project_id=project, plan_id=plan, validate_placements=demo_validator,
            placement_context=context, rules=rules)

bad = ifc_modify_device("A", {"position": b["position"]}, expected_version=1, **opts)
print("Trùng vị trí:", bad["status"], bad["validation_result"]["errors"])
assert bad["status"] == "validation_failed"
assert ProjectStore().get_placement_plan(project, plan)["version"] == 1

edited = ifc_modify_device("A", {"orientation": 90}, expected_version=1, **opts)
print("Sửa bản nháp:", edited["status"], edited["version"])
assert edited["status"] == "pending_review" and edited["version"] == 2, edited
stale = ifc_modify_device("A", {"orientation": 45}, expected_version=1, **opts)
assert stale["status"] == "conflict", stale

approve_placement_plan(project, plan, 2, reviewer_id="engineer-demo")
first = apply_approved_placement_plan(plan, 2, project)
assert first["status"] == "applied" and first["model_version"] == 1, first
guid = first["created_ids"][0]
path1 = get_project_ifc_path(project, 1)

moved = [-7.5, -3.0, 2.7]
edit_ifc = ifc_modify_device(guid, {"position": moved}, target="ifc", expected_version=2, **opts)
print("Sửa thiết bị đã đặt:", edit_ifc["status"], edit_ifc["version"])
assert edit_ifc["status"] == "pending_review" and edit_ifc["version"] == 3, edit_ifc
assert apply_approved_placement_plan(plan, 3, project)["status"] == "failed"
assert get_project_ifc_path(project) == path1

approve_placement_plan(project, plan, 3, reviewer_id="engineer-demo")
second = apply_approved_placement_plan(plan, 3, project)
assert second["status"] == "applied" and second["model_version"] == 2, second
assert second["updated_ids"] == [guid] and not second["created_ids"] and not second["removed_ids"]
path2 = get_project_ifc_path(project, 2)
model = open_model(path2)
device = model.by_guid(guid)
xyz_mm = get_local_placement(device.ObjectPlacement)[:3, 3]
assert all(abs(value / 1000 - expected) < 1e-7 for value, expected in zip(xyz_mm, moved))
assert get_container(device, should_get_direct=True).GlobalId == space
assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
print("ĐẠT. GlobalId giữ nguyên:", guid)
print("IFC trước khi dời A:", path1)
print("IFC sau khi dời A:", path2)
```

### Tiêu chí đạt

| Bài thử | Kết quả |
| --- | --- |
| Dời A trùng tọa độ B | `validation_failed`, phiên bản nháp vẫn là 1. |
| Xoay A trong nháp | `pending_review`, phương án lên phiên bản 2. Gửi tiếp với phiên bản gốc 1 trả `conflict`. |
| Dời A bằng IFC GlobalId | Phương án lên phiên bản 3, IFC vẫn là model phiên bản 1 cho đến khi duyệt. |
| Duyệt và áp dụng bản sửa | Model phiên bản 2, A giữ GlobalId và có tọa độ mới `[-7.5, -3.0, 2.7]` mét. Không thêm hoặc xóa thiết bị. |
| Đối chiếu tệp | Mở hai đường dẫn đã in trong viewer để xem A trước và sau khi dời. Hash của `A.ifc` khớp với trước khi chạy. |

Kiểm thử tự động riêng tool bằng `py -m unittest discover -s tests -p test_ifc_modify_device.py -v`.
