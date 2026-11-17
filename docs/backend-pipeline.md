# Backend đặt thiết bị điện

## Phạm vi và luồng chạy

LLM tự đề xuất thiết bị và tọa độ. Không có `engine_plan_placements` trong code
hay registry. Pipeline hiện hoạt động đến đặt, duyệt và xuất thiết bị:

```text
Upload IFC4 → bảng hỏi → PLACING_DEVICES → REVIEW_DEVICES
                        ↑                    |
                        └── yêu cầu sửa ─────┤
                                             ↓ duyệt đúng plan_id/version
                                      APPLYING_DEVICES
                                             ↓
                                      DEVICES_APPROVED
```

Orchestrator điều khiển phase bằng code, không theo câu trả lời của LLM. Chỉ một
bản nháp thực sự đã lưu mới đưa phase sang review. LLM không được gọi công cụ
duyệt hoặc apply IFC. Kỹ sư có thể yêu cầu sửa bằng `message` và `feedback` ở
endpoint agent. Agent đọc `draft_get_placements` để giữ ID khi sửa.

Agent gọi tool tuần tự; registry kiểm tra schema và không nhận đường dẫn IFC,
project ID hoặc storage client từ LLM. Phiên bản bảng hỏi và IFC được ghim theo
lượt chạy. Thay đổi bảng hỏi sau khi tạo nháp sẽ chặn duyệt nháp cũ.

## Cài đặt và khởi động

Python 3.12, chạy tại thư mục gốc trong PowerShell:

```powershell
uv sync --group dev
# Nếu cần tools upstream IfcMCP:
uv sync --group dev --extra ifcmcp
Copy-Item .env.example .env  # Chỉ thực hiện nếu chưa có .env
```

Điền Supabase và provider/model trong `.env`. Provider `openai` hiện dùng
OpenRouter (`OPENROUTER_API_KEY`); `groq` và `gemini` dùng khóa tương ứng.
`LLM_MODEL` phải được khai báo rõ; backend không tự chọn model có thể tính phí.
`PLAYBOOK_PATH` là workbook có sheet `Lớp phụ tải`, dùng cho checklist và tính
tải. Tra quy tắc/catalog thiết bị vẫn dùng các bảng Playbook trên Supabase.

`SUPABASE_KEY` của backend phải là **Secret API key** (`sb_secret_...`) hoặc
legacy `service_role` JWT. Publishable/anon chỉ phù hợp luồng đọc công khai; không
đủ quyền cho backend này. `check-config --require-supabase` kiểm tra định dạng
khóa server, chưa xác minh kết nối hay quyền thực tế.

Sau khi cấu hình khóa và áp dụng migration, chạy kiểm tra chỉ đọc:

```powershell
.venv\Scripts\python.exe -m electrical_design check-backend
```

Kết quả cần `status: ok` cho các bảng và bucket. Lệnh không thử INSERT/upload và
không tự sửa database. Nếu bucket `ifc-files` chưa có, tạo bucket private bằng
migration project hoặc SQL sau trong Supabase SQL Editor:

```sql
insert into storage.buckets (id, name, public)
values ('ifc-files', 'ifc-files', false)
on conflict (id) do nothing;
```

Khi chẩn đoán upload bằng PowerShell, kiểm tra `$LASTEXITCODE` ngay sau curl rồi
mới `ConvertFrom-Json`, để không che lỗi HTTP gốc. API trả JSON 503 với mã
`SUPABASE_SCHEMA_MISSING`, `SUPABASE_PERMISSION_DENIED` hoặc
`SUPABASE_STORAGE_UNAVAILABLE` khi dependency chưa được chuẩn bị đúng.

Áp dụng các migration trong `supabase/migrations` theo thứ tự lên Supabase dev.
Schema mới có `projects`, `project_files`, bảng revision bảng hỏi và
`phien_ban_phuong_an_bo_tri`; bucket `ifc-files` là private. Không chạy lại
migration đã có trên database hiện hữu; đối chiếu schema/migration history trước.

```powershell
.venv\Scripts\python.exe -m uvicorn api.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

OpenAPI tại `http://127.0.0.1:8000/docs`. Đây là backend cho **một workspace kỹ
sư**, dùng `X-API-Key` khi có `API_TOKEN`; production bắt buộc có token. Giá trị
`reviewer_id` là UUID ghi nhận người duyệt do client tin cậy cung cấp, chưa phải
identity từ hệ thống đăng nhập đa người dùng. WebSocket cũng nhận `X-API-Key`
qua header cho client Python. Giao diện demo tích hợp gửi API_TOKEN qua
WebSocket subprotocol `volt-token.<base64url>` và chọn protocol `volt-demo`,
không đưa token vào URL; không cần proxy riêng khi dùng giao diện này.

## API sử dụng

1. `POST /projects`: multipart `name`, `file`. Chấp nhận IFC4 có IfcSpace,
   tối đa 250 MiB. Backend tạo bản sao riêng, upload storage và trả `project_id`.
2. `GET /projects/{id}/questionnaire/template`: mẫu nhập tay; dùng metadata trả
   về để `POST .../submit`. Checklist dùng `.../checklist-template` và
   `.../checklist-submit`, cần `PLAYBOOK_PATH`.
3. `POST /projects/{id}/agent/run`:

   ```json
   {"message": "Đề xuất bố trí thiết bị điện theo bảng hỏi", "feedback": ""}
   ```

4. `GET /projects/{id}/review`: xem proposals, validation, diff và version.
5. `POST /projects/{id}/review/approve`:

   ```json
   {"plan_id": "UUID từ bản nháp", "version": 1, "reviewer_id": "UUID kỹ sư"}
   ```

6. `GET /projects/{id}/ifc`: tải snapshot IFC hiện hành. Snapshot điện chứa cả
   kiến trúc gốc; không cần merge mù hai file có hierarchy trùng nhau.

WebSocket `ws://127.0.0.1:8000/projects/{id}/agent/ws`: gửi object như agent REST,
nhận `phase`, `message`, `tool_start`, `tool_result`, `done`/`error`. Gửi
`{"action":"cancel"}` để hủy. Khi một tool lưu DB đã bắt đầu, backend đợi tool
hoàn tất trước khi trả điều khiển để giữ checkpoint nhất quán. Đây là streaming
event theo lượt, chưa phải streaming từng token của provider.

## Hợp đồng proposal

```json
{
  "proposed_id": "room-1-outlet-01",
  "device_type": "outlet",
  "space_id": "IfcSpace.GlobalId",
  "host_id": "IfcWall/IfcSlab/...GlobalId",
  "position": {"x": 1.0, "y": 0.05, "z": 0.3},
  "orientation": {"yaw": 90.0},
  "rule_ids": [],
  "properties": {}
}
```

Tọa độ **mét, model_world tuyệt đối**, yaw theo độ quanh Z. Không truyền vector
orientation x/y/z và không cộng cao độ tầng lần hai. `space_id/host_id` là IFC
GlobalId; `device_id` của bản nháp là UUID; STEP ID từ IfcMCP chỉ dùng trong đúng
snapshot đang đọc. Writer hỗ trợ outlet/socket, switch, light/light_fixture,
junction_box, appliance và panel; registry phase này không cung cấp luồng thiết
kế tủ. Hình học mặc định của thiết bị là khối hộp minh họa; dùng `dimensions_m`
và catalog xác thực khi có dữ liệu thật.

`draft_save_placements` nhận **toàn bộ danh sách phương án**, tự kiểm tra lại và
lưu revision pending_review, kể cả bản còn lỗi để không mất công việc. Thiết bị
bị bỏ khỏi revision mới được coi là yêu cầu xóa. Backend cấp device UUID ổn định
từ plan/proposed_id; IFC GlobalId cũng ổn định khi sửa/gọi lại.

Validator hiện kiểm tra cấu trúc, tọa độ hữu hạn, ID phòng/host, bbox phòng,
hướng yaw, gần trùng và khả năng va bbox vật cản. `complete=false` và cảnh báo
luôn được trả. Chưa có kiểm tra chính xác polygon phòng, solid clash thiết bị,
pháp tuyến host, vùng mở cửa hay thực thi toàn bộ quy tắc Playbook. `valid=true`
không chứng nhận thiết kế đúng quy chuẩn. Kỹ sư cần xem các cảnh báo trước khi
duyệt. Backend chạy lại validation trên IFC hiện hành lúc duyệt và lúc apply.

## Lưu trữ và phục hồi

- SQLite `STATE_DB`: phase, lịch sử chat và checkpoint plan/version sau **mỗi**
  lần lưu/sửa/xóa nháp. Giữ file này và thư mục `WORKSPACE_DIR` khi khởi động lại.
- Supabase: revision bất biến, quyết định duyệt, metadata IFC và checksum.
- Apply ghi sang file tạm mới, mở lại kiểm chứng, upload và kiểm tra SHA256 rồi
  mới chuyển pointer hiện hành. Lỗi không ghi đè `ctr.ifc` hoặc snapshot trước.
- Retry cùng revision đã apply trả `already_applied`. Crash ở APPLYING_DEVICES
  có thể retry endpoint approve với đúng plan/version; không tự chạy phase sau.
- Migration khóa hàng project để tuần tự hóa commit giữa tiến trình. Runtime
  dùng SQLite và một worker; triển khai nhiều host cần chuyển runtime state sang
  Postgres. Upload thất bại/commit bị cạnh tranh có thể để file orphan, cần dọn
  theo metadata sau khi kiểm tra không còn tham chiếu.

## IfcMCP

Adapter dùng thư viện chính thức [IfcOpenShell IfcMCP](https://docs.ifcopenshell.org/ifcmcp.html),
commit đã ghim trong `pyproject.toml`/`uv.lock`; không cần process MCP bên ngoài.
Package phân phối là `ifcopenshell-mcp`, import là `ifcmcp`. Bật
`ENABLE_IFCMCP=true` sau khi cài extra. Mỗi lượt chạy có session riêng, load đúng
snapshot project. Allowlist chỉ gồm `ifc_summary`, `ifc_tree`, `ifc_info`,
`ifc_select`, `ifc_relations`, `ifc_contexts`, `ifc_materials`, `ifc_schema`,
`ifc_clash`, `ifc_validate`. Không đưa `ifc_edit`, `ifc_shape`, `ifc_quantify`,
save/load tùy ý hoặc lifecycle tools cho LLM. Các truy vấn rất lớn có thể bị
provider cắt nội dung; dùng `ifc_search_elements` có phân trang khi cần.

## Kiểm thử

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest -q --live
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
```

Mặc định bỏ qua bốn bài Supabase live; `--live` chỉ bật các bài đọc Playbook.
IfcMCP tests skip nếu chưa cài extra. Tests dùng IFC thật tạo nhỏ với đơn vị mm,
storage double và scripted LLM để tái lập lỗi, test toàn pipeline và failure path;
có round-trip bản sao `ctr.ifc`. Không cần khóa LLM để chạy regression suite.
Test ghi lên Supabase thật cũ tại `test/test_ifc_write.py` vẫn là script opt-in,
tạo bản ghi có lưu lại; không thuộc suite mặc định.

SLD và routing chưa được mở thành phase của pipeline. Hai module có tiện ích
preview từ schedule/đường trực giao do người dùng cung cấp, trả validated=false;
chưa phải bộ thiết kế tủ hoặc giải tuyến tránh vật cản.
