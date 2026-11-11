# Phase 2 — Áp dụng phương án bố trí đã duyệt vào IFC

`ifc_apply_placements_batch` nhận `approved_plan_id`, `version` và `project_id`. Orchestrator gọi qua `apply_approved_placement_plan` sau khi backend ghi quyết định duyệt của kỹ sư. Kết quả gồm `status`, các danh sách GlobalId `created_ids`, `updated_ids`, `removed_ids`, `errors` và `model_version`.

## Nguyên lý

1. Backend đăng ký một bản sao IFC kiến trúc làm model phiên bản 0. Khi kỹ sư duyệt, backend ghi nhận danh tính người duyệt và đúng phiên bản phương án đã vượt qua kiểm tra. Mỗi phiên bản nháp có quyết định duyệt riêng.
2. Orchestrator so sánh danh sách thiết bị được duyệt với lần áp dụng gần nhất của cùng phương án. Thiết bị được nhận diện bằng `proposed_id` ổn định. Cùng mã giữ cùng IFC GlobalId khi đổi vị trí hoặc loại IFC. Đổi mã được tính là xóa thiết bị cũ và thêm thiết bị mới.
3. Writer ghi model bằng IfcOpenShell. Nó tạo hoặc dùng lại IFC Device Type theo dự án, lớp IFC và `module_id` của catalog. Thiết bị thuộc `IfcSpace`, có quan hệ với host nếu được khai báo và có property set `MVP_Placement`. Khi cập nhật hoặc xóa thiết bị có tham chiếu ngoài như nhóm mạch, tool báo lỗi để bảo toàn liên kết đó.
4. Writer mở lại IFC vừa ghi để đối chiếu lớp IFC, type, vị trí, hướng, phòng, host, hình học và thông tin đã lưu. Project DB chỉ công bố model mới và ghi nhận đã áp dụng sau khi đối chiếu thành công. Tệp kiến trúc và các phiên bản model trước được giữ để mở lại.

Lần chạy thành công trả `applied`. Gọi lại cùng phiên bản đã áp dụng trả `already_applied`, ba danh sách rỗng và phiên bản model hiện tại. Một phiên bản phương án mới có nội dung thiết bị giống bản đã áp dụng vẫn được ghi nhận, đồng thời giữ nguyên `model_version`. Khi thất bại, `status` là `failed`, `model_version` là `None` và lý do nằm trong `errors`. Tệp nào dùng được xác định qua Project DB. Nếu tiến trình dừng sau khi tạo tệp nhưng trước khi PostgreSQL commit, thư mục lưu trữ có thể còn một tệp chưa được tham chiếu.

## Hợp đồng dữ liệu và tích hợp

- Writer hiện hỗ trợ **IFC4**. `space_id` phải là GlobalId của `IfcSpace`. `host_id` nếu có phải là GlobalId của một `IfcBuildingElement`, ví dụ tường, sàn hoặc trần mô hình bằng `IfcCovering`.
- `position` là XYZ tính bằng mét trong hệ tọa độ model IFC. `orientation` nhận góc yaw tính bằng độ quanh trục Z của model dưới dạng số hoặc `{"yaw": 90}`. Mặc định yaw là 0. IfcOpenShell chuyển vị trí sang đơn vị độ dài của file.
- `device_type` có thể là `socket`, `outlet`, `switch`, `light`, `light_fixture`, `junction_box`, `appliance` hoặc `panel`. Cũng có thể truyền lớp IFC tương ứng, qua `device_type` hoặc trường `ifc_class` có độ ưu tiên cao hơn.
- Hình học hiện dùng khối hộp đặt theo trục local của thiết bị. `dimensions_m` tùy chọn theo thứ tự chiều dài X, độ dày Y, chiều cao Z. Mỗi lớp có kích thước mặc định để xem trong viewer. Payload giữ `geometry_ref` nếu có, việc nạp hình học catalog cần tích hợp thêm.
- `register_project_ifc` và `approve_placement_plan` là hàm nội bộ. API thật phải xác thực kỹ sư, kiểm tra quyền dự án và lấy `reviewer_id` từ phiên đăng nhập. Tool ghi IFC thuộc Orchestrator và nằm ngoài danh sách tool của LLM. Repo hiện chưa nối các hàm này vào API upload, review hoặc engine kiểm tra hình học.
- Các tệp phiên bản IFC nằm ở `MVP_IFC_SNAPSHOT_DIR`. PostgreSQL lưu tên file và hash. Lấy đường dẫn bằng `get_project_ifc_path`.

## Kiểm tra thủ công trong VS Code

Mở thư mục MVP và chuẩn bị `MVP_TEST_DATABASE_URL` trỏ tới database thử đã áp dụng migration theo [hướng dẫn Supabase](placement_supabase.md). Trong Terminal PowerShell, dùng `py` như khi kiểm tra tool lưu nháp.

```powershell
cd D:\AI_CONSTRUCTTION\MVP
py --version
py -m pip install -r requirements-ifc.txt
$env:MVP_DATABASE_URL = $env:MVP_TEST_DATABASE_URL
$env:MVP_IFC_SNAPSHOT_DIR = Join-Path $env:TEMP ("mvp-ifc-" + [guid]::NewGuid())
$env:MVP_TEST_IFC = Join-Path $env:TEMP ("mvp-source-" + [guid]::NewGuid() + ".ifc")
py tests/ifc_fixture.py $env:MVP_TEST_IFC
Get-FileHash -Algorithm SHA256 -LiteralPath $env:MVP_TEST_IFC
py
```

### 1. Thử trước và sau khi duyệt

Dán vào dấu nhắc `>>>` của Python. Dữ liệu kiểm tra và người duyệt được giả lập trong terminal.

```python
import os
from uuid import uuid4

import ifcopenshell
from agent.orchestrator import apply_approved_placement_plan
from database.placement_application import approve_placement_plan, get_project_ifc_path, register_project_ifc
from tools.ifc_write import draft_save_placements

project = f"manual-ifc-{uuid4().hex}"
source = os.environ["MVP_TEST_IFC"]
model = ifcopenshell.open(source)
space = model.by_type("IfcSpace")[0].GlobalId
host = model.by_type("IfcWall")[0].GlobalId
register_project_ifc(project, source)

check = {"valid": True, "errors": [], "warnings": [], "suggested_fixes": []}
a = {"proposed_id": "A", "device_type": "socket", "module_id": "S01", "space_id": space, "host_id": host, "position": [4.86, -1, 3.35], "orientation": {"yaw": 90}}
b = {**a, "proposed_id": "B", "position": [4.86, 0, 3.35]}
v1 = draft_save_placements(project, [a, b], check)
plan_id = v1["plan_id"]

print(apply_approved_placement_plan(plan_id, 1, project))
approve_placement_plan(project, plan_id, 1, reviewer_id="engineer-demo")
first = apply_approved_placement_plan(plan_id, 1, project)
print(first)
print(get_project_ifc_path(project))
print(apply_approved_placement_plan(plan_id, 1, project))
```

Lần đầu trả `failed` vì chưa duyệt. Sau duyệt, kết quả phải là `applied`, `model_version: 1`, hai GlobalId trong `created_ids`. Chạy lại trả `already_applied` và không tạo thêm thiết bị.

### 2. Dời A, bỏ B, thêm C

```python
moved = {**a, "position": [4.86, -0.5, 3.35]}
c = {**a, "proposed_id": "C", "position": [4.86, 1, 3.35]}
v2 = draft_save_placements(project, [moved, c], check, plan_id=plan_id, expected_version=1)
approve_placement_plan(project, plan_id, v2["version"], reviewer_id="engineer-demo")
second = apply_approved_placement_plan(plan_id, v2["version"], project)
print(second)
print(second["updated_ids"] == [first["created_ids"][0]])
print(second["removed_ids"] == [first["created_ids"][1]])
print(get_project_ifc_path(project, 1))
print(get_project_ifc_path(project, 2))
```

Kết quả có một ID thêm, một ID sửa, một ID xóa và hai phép so sánh in `True`. Mở đường dẫn version 1 và 2 trong IFC viewer để đối chiếu. Version 1 còn B và A ở vị trí cũ. Version 2 có C và A ở vị trí mới.

### 3. Buộc lỗi giữa một lô

```python
bad = {**c, "proposed_id": "Z", "host_id": "missing-host"}
v3 = draft_save_placements(project, [a, c, bad], check, plan_id=plan_id, expected_version=2)
approve_placement_plan(project, plan_id, v3["version"], reviewer_id="engineer-demo")
print(apply_approved_placement_plan(plan_id, v3["version"], project))
print(get_project_ifc_path(project) == get_project_ifc_path(project, 2))
```

Kết quả phải báo `failed` với lỗi `host_id`, và phép so sánh phải in `True`. Gõ `exit()` để trở lại PowerShell rồi chạy lại `Get-FileHash -Algorithm SHA256 -LiteralPath $env:MVP_TEST_IFC`. Hash của tệp gốc phải giống lần đầu.

### Dùng file IFC của bạn

Trỏ `$env:MVP_TEST_IFC` đến tệp IFC4 của bạn và dùng cơ sở dữ liệu thử đã áp dụng migration. Chọn một `IfcSpace` và host đúng phòng bằng GlobalId, rồi thay tọa độ mẫu bằng tọa độ lấy từ model đó. Kiểm tra vị trí trong viewer, room containment và property set `MVP_Placement`. Thử bằng dữ liệu thực giúp xác nhận quy ước tọa độ giữa công cụ xuất IFC của bạn và writer. Kết quả kiểm tra va chạm, khoảng cách an toàn và quy tắc bố trí cần lấy từ engine kiểm tra của dự án.

Chạy lại toàn bộ kiểm thử tự động từ PowerShell bằng `py -m unittest discover -s tests -v`.
