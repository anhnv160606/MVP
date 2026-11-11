# MVP

## Phase 2 — Lưu nháp phương án bố trí thiết bị

Chạy từ thư mục gốc của MVP bằng Python 3.11 trở lên.

```python
from database.project_store import ProjectStore
from tools.draft_save_placements import draft_save_placements

proposals = [{
    "proposed_id": "socket-01",
    "device_type": "socket",
    "space_id": "room-01",
    "host_id": "wall-02",
    "position": {"x": 1.2, "y": 0.0, "z": 0.35},
    "orientation": {"yaw": 90},
    "rule_ids": ["RULE-01"],
    "reason": "Gần vị trí bàn làm việc",
}]
validation_result = {"valid": True, "errors": [], "warnings": [], "suggested_fixes": []}
saved = draft_save_placements("project-01", proposals, validation_result)
snapshot = ProjectStore().get_placement_plan("project-01", saved["plan_id"], saved["version"])
```

- Lần gọi với ba tham số bắt buộc tạo phương án mới ở phiên bản 1. Để lưu bản chỉnh sửa, truyền thêm `plan_id=saved["plan_id"]` và `expected_version=saved["version"]` cùng toàn bộ danh sách đề xuất và kết quả kiểm tra tương ứng. Nếu phiên bản đã thay đổi, tool báo `PlacementVersionConflict`. Plan ID thuộc dự án khác hoặc không tồn tại báo `PlacementPlanNotFound`.
- Mỗi bản lưu có trạng thái `pending_review`, kể cả khi kết quả kiểm tra là `valid: false`. Dữ liệu lý do và cảnh báo được lưu trong payload gốc. Tool kiểm tra cấu trúc dữ liệu. Việc kiểm tra hình học và quy tắc thiết kế thuộc về `engine_validate_placements`.
- `diff_summary` trả về các danh sách `proposed_id` đã sắp xếp trong `added`, `updated`, `removed` và `unchanged`. So sánh dựa trên toàn bộ nội dung đề xuất và bỏ qua thứ tự của danh sách thiết bị. Danh sách đề xuất rỗng biểu diễn phương án không còn thiết bị.
- SQLite mặc định lưu tại `database/project.db`. Dùng `MVP_PROJECT_DB_PATH` để đặt đường dẫn khác. `ProjectStore.get_placement_plan(project_id, plan_id)` đọc bản mới nhất, hoặc truyền thêm `version` để đọc lịch sử. Tool chỉ ghi dữ liệu nháp vào Project DB.

```sh
python -m unittest discover -s tests -v
```

Các bài kiểm thử IFC trong bộ test cần cài thêm dependency theo [hướng dẫn áp dụng phương án vào IFC](docs/ifc_apply_placements_batch.md). Để chỉ chạy bộ kiểm thử lưu nháp, thêm `-p test_draft_save_placements.py` vào lệnh trên.

Xem [nguyên lý và bài kiểm thử chỉnh sửa thiết bị trên A.ifc](docs/ifc_modify_device.md) để thử `ifc_modify_device`.
