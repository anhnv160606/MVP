# MVP

## Backend đặt thiết bị điện

Đã có [frontend demo local](docs/frontend-demo.md). Khi chạy backend, mở
**http://127.0.0.1:8000** để upload, nhập nhu cầu, theo dõi Agent và duyệt IFC.
Muốn xem giao diện với dữ liệu mẫu khi chưa cấu hình Supabase/LLM:

```powershell
.venv\Scripts\python.exe -m electrical_design preview --port 8001
```

Mở **http://127.0.0.1:8001** và chọn **Xem dữ liệu mẫu**.

Pipeline hiện nối upload IFC → bảng hỏi → Agent tự đề xuất → kiểm tra/lưu nháp
→ kỹ sư duyệt → ghi IFC. Xem [hướng dẫn backend](docs/backend-pipeline.md),
[danh sách tools](docs/tools_list.md) và [bàn giao phiên làm việc](session-handoff.md).

```powershell
uv sync --group dev --extra ifcmcp
.venv\Scripts\python.exe -m uvicorn api.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Khai báo `LLM_PROVIDER`, `LLM_MODEL` và khóa tương ứng trong `.env`; áp dụng các
migration Supabase trước khi chạy. Bật `ENABLE_IFCMCP=true` để mở tools đọc
upstream. Chạy `.venv\Scripts\python.exe -m pytest -q` để kiểm thử độc lập,
thêm `--live` để kiểm thử chỉ đọc Playbook trên Supabase.

## Phase 1 · Bảng hỏi thiết bị

Cài thư viện từ thư mục gốc của repo bằng Python 3.12.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-questionnaire.txt
```

Cấu hình `SUPABASE_URL` và `SUPABASE_KEY` trong `.env` của backend. Dùng khóa server có role `service_role` trên Supabase dev. Áp dụng migration `supabase/migrations/20261116010000_phase1_questionnaire.sql` trước khi chạy demo local. API dùng Supabase Client sẵn có của repo.

Chuẩn bị IFC và workbook Playbook có sheet **Lớp phụ tải**. Thay các đường dẫn minh họa bằng file thực tế. Hai JSON mẫu tham chiếu ID phòng trong bản `A.ifc` của nhóm.

```sh
.venv/bin/python -B -m api.routes.questionnaire \
  --ifc /duong-dan/A.ifc \
  --playbook /duong-dan/Playbook.xlsx \
  --project-id checklist-demo --port 8000
```

Mở [bảng hỏi](http://127.0.0.1:8000/projects/checklist-demo/questionnaire/demo). Chọn thiết bị theo phòng hoặc toàn công trình, bấm **Lưu bảng hỏi** rồi **Tải JSON**. Nếu muốn thao tác bằng terminal, giữ server đang chạy và mở terminal thứ hai:

```sh
.venv/bin/python -B -m tools.phase1.checklist_terminal \
  --url http://127.0.0.1:8000 --project-id checklist-demo --output-dir ~/Downloads
```

Chọn **Lưu bảng hỏi và xuất JSON** để ghi file `phase1-<project-id>-rev-<revision>.json`.

Luồng API dùng ba tool theo thứ tự:

1. `questionnaire_get_template` tạo mẫu với danh sách phòng đọc từ IFC và lựa chọn từ Playbook. Bản checklist dùng `GET /checklist-template`.
2. `questionnaire_submit` kiểm tra lựa chọn và lưu phiên bản vào PostgreSQL. Bản checklist dùng `POST /checklist-submit`.
3. `questionnaire_get_load_context` đọc nhu cầu đã chuẩn hóa qua `GET /load-context`. Phase 2 truyền `questionnaire_revision` để dùng đúng phiên bản đã chọn. Bỏ `space_id` để lấy toàn dự án, gồm nhu cầu chưa gán phòng.

Các endpoint trên nằm dưới `/projects/{project_id}/questionnaire`. Xem [OpenAPI](http://127.0.0.1:8000/docs) và [đặc tả Phase 1](phase1_questionnaire.md).

### Thử hai revision bằng JSON mẫu

Chạy lại lệnh khởi động API ở trên với `--ifc /duong-dan/A.ifc` và một `--project-id` mới, ví dụ `questionnaire-example`. Project này phục vụ luồng nhập tay. File đầu tiên dùng `expected_revision: 0`, nên hãy chọn một project chưa có bảng hỏi.

Từ thư mục gốc của repo, mở terminal thứ hai và chạy:

```sh
BASE=http://127.0.0.1:8000/projects/questionnaire-example/questionnaire
.venv/bin/python -B - "$BASE" <<'SAMPLE'
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

base = sys.argv[1]
with urlopen(f"{base}/template") as response:
    template = json.load(response)

keys = ("template_id", "template_version", "model_revision")
metadata = {key: template[key] for key in keys}

for revision in (1, 2):
    path = Path(f"examples/questionnaire-rev{revision}.json")
    sample = json.loads(path.read_text(encoding="utf-8"))
    request = Request(
        f"{base}/submit",
        data=json.dumps({**sample, **metadata}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request) as response:
        result = json.load(response)
    print(f"Đã lưu revision {result['questionnaire_revision']}")
SAMPLE

for revision in 1 2; do
  curl -fsS "$BASE/load-context?questionnaire_revision=$revision" |
    .venv/bin/python -m json.tool
done
```

Revision 1 có một thiết bị bàn làm việc 120 W. Revision 2 cập nhật cùng nhu cầu thành hai thiết bị, mỗi thiết bị 150 W, và thêm một bình nước nóng chưa biết công suất. Hai JSON trong `examples/` là đầu vào để nộp bảng hỏi. JSON Phase 2 sử dụng là kết quả của `/load-context` theo revision đã chọn.

## Tool #14 · Tính phụ tải sơ bộ

Phase 2 dùng JSON bảng hỏi để lên phương án bố trí. Tool #14 nhận snapshot của phương án có placement và requirement đã ánh xạ sang `room_id` và `load_class_id`. Snapshot dùng `plan_id`, `plan_revision` và các danh sách `placements`, `requirements`; thêm `zones` nếu tính theo zone, hoặc `scenarios` nếu đã xác định kịch bản vận hành.

```sh
.venv/bin/python -B -m tools.calc_preliminary_load \
  --ifc /duong-dan/A.ifc \
  --playbook /duong-dan/Playbook.xlsx \
  --input /duong-dan/snapshot-phuong-an.json \
  --scope-type room --scope-id '<IfcSpace.GlobalId>'
```

Dùng `floor` với GlobalId của tầng, hoặc `zone` với mã zone trong snapshot. Thêm `--connected-only` để bỏ áp dụng hệ số nhu cầu. Kết quả JSON gồm tải kết nối W/VA, phụ tải tính toán, chi tiết từng tải, độ đầy đủ của dữ liệu và cảnh báo. CLI đọc catalog Excel; nếu phương án có lớp tải tùy chỉnh, luồng Python truyền catalog đã gộp qua `calculation_playbook(catalog)`.
