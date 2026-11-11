# MVP – Agent thiết kế điện từ IFC

Repo tách ba nhóm dữ liệu: IFC của công trình, Playbook tra cứu trên Supabase và trạng thái phương án dự án. Tài liệu kiến trúc/Playbook đầu vào nằm ngoài repo ở thư mục workspace cấp trên.

## Phase 2: bố trí thiết bị

Các module trong `tools/` là tool layer cho Agent:

- `ifc_read.py`: context không gian và danh sách thiết bị read-only; giải mã tên IFC STEP, chuẩn hóa đơn vị và có fallback ứng viên theo bounding box cùng tầng khi IFC thiếu quan hệ ranh giới phòng.
- `questionnaire.py`: nhu cầu tải do người dùng khai báo.
- `playbook.py`: truy vấn read-only các bảng Playbook Supabase.
- `placement.py`: sinh ứng viên tọa độ có căn cứ và kiểm định hình học.
- `ifc_write.py`: lưu bản nháp, chỉnh sửa/xóa và ghi IFC sau duyệt.
- `calculation.py`: phụ tải nối và phụ tải tính toán sơ bộ.
- `phase2.py`: schema 14 tool và bộ dispatch có dependency injection cho Agent.
- `agent/agent.py`: vòng gọi model → tools → model, không khóa vào một nhà cung cấp.

`database/project_store.py` giữ câu trả lời bảng hỏi và các phiên bản nháp trong SQLite; Playbook không bị sao chép sang SQLite. Mặc định DB trạng thái được tạo tại `database/project_state.db` khi ứng dụng khởi tạo store; có thể đổi bằng biến môi trường `PROJECT_STATE_DB`.

## Nối vào ứng dụng

Tầng API khởi tạo một `ProjectStore`, Supabase client đã xác thực, IFC model provider và model adapter. Sau đó gọi:

```python
from database.project_store import ProjectStore
from tools.phase2 import build_phase2_toolset

phase2 = build_phase2_toolset(
    project_id=project_id,
    store=ProjectStore(),
    supabase_client=supabase,
    ifc_model_provider=current_ifc_model,
    model_adapter_provider=current_ifc_adapter,
    is_plan_approved=lambda plan_id, version: store.is_plan_approved(project_id, plan_id, version),
)
# Gửi phase2["tools"] cho LLM; thực thi tool call qua phase2["execute"].
```

`ToolCallingAgent` nhận callback `complete(messages=..., tools=...)` từ provider và `phase2` làm toolset. System prompt, provider credentials, lịch sử lưu trữ và streaming vẫn do tầng API/app cấp.

## Chạy smoke test Tool 1–6

Chạy từng file từ thư mục `MVP`. Tool 1 đọc `A.ifc` ở thư mục cha (cần cài `ifcopenshell`); Tool 2–6 dùng dữ liệu mẫu đại diện lấy từ Playbook nên không cần kết nối Supabase.

```powershell
python tests\test_ifc_get_placement_context.py
python tests\test_questionnaire_get_load_context.py
python tests\test_playbook_get_object_rules.py
python tests\test_playbook_query_devices.py
python tests\test_playbook_get_load_class.py
python tests\test_playbook_get_terminal_class.py
```

Mỗi file in JSON theo output của tool để dễ đối chiếu. Có thể đổi IFC của Tool 1 bằng biến môi trường `A_IFC_PATH`.

IFC adapter phải cung cấp thao tác transaction an toàn `apply_batch(records, idempotency_key, removed_ids)`. Có thể bổ sung `list_devices()`, `modify_device(id, changes)` và `remove_device(id, reason)` để nối đầy đủ các thao tác. `IfcOpenShellPlacementAdapter` đã có khung ghi theo transaction; ứng dụng cần cấp `geometry_factory` từ catalog và `output_path`. Adapter chịu trách nhiệm rollback khi IFC write lỗi; tool chỉ đánh dấu phương án `applied` sau khi adapter xác nhận `status="success"`. Kỹ sư/API review gọi `ProjectStore.approve_plan(project_id, plan_id, version)` sau xác thực người duyệt; không expose thao tác duyệt cho LLM.

## Ranh giới dữ liệu

Playbook hiện có dữ liệu quy tắc phòng, phân loại phụ tải/đầu cuối và mô-đun thiết bị. Một số trường thiết kế như số lượng bắt buộc, khoảng cách, hình học catalog, công suất thiết bị và hướng mở cửa không luôn có trong nguồn. Tool trả cảnh báo và không tự điền các giá trị này. Vì thế phương án chỉ được sinh khi đã có geometry, host surface, cao độ và quantity rõ ràng; kỹ sư vẫn cần duyệt trước khi ghi IFC.

Các migration của Supabase nằm trong `supabase/migrations/` và được giữ nguyên trong lần triển khai tool này.
