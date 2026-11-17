# Kết quả kiểm chứng — 17/11/2026

## Kết quả

- `pytest -q --live -m 'not ifc'`: **30 passed, 2 deselected** (8,51 giây).
- Hai bài IFC thật đã đạt: đọc placement context và round-trip bản sao `ctr.ifc`.
  Round-trip riêng mất khoảng 86 giây; phần lớn thời gian là tessellation hình học
  của model 480.770 entities, không phải vòng lặp Agent bị treo.
- Lượt full suite trước khi thêm bài upload: **31 passed** (94,53 giây).
  Bài upload mới đã đạt trong lượt 30 bài nhanh ở trên. Tổng hiện tại: **32 ca**.
- Ruff lint: đạt. Ruff format: **72 file** đúng định dạng.
- Compile Python: đạt.
- Wheel: `dist/electrical_design_agent-0.1.0-py3-none-any.whl`, build đạt.
  Kiểm tra 57 entries: có agent/runtime/API/tools, không có `.env`, IFC hoặc cache.
- Export/kiểm tra **30 tool schemas**: không lộ đường dẫn project, không có tool
  tự duyệt/apply/edit IFC hoặc sinh tọa độ bằng engine.

## Phạm vi bằng chứng

| Nhóm | Kiểm tra |
|---|---|
| Playbook 4 tools | Đọc Supabase thật; catalog, room/object rules, load class, terminal class. Thêm offline test cho phân trang >1.000 hàng, ambiguous match và điện áp. |
| Bảng hỏi 3 tools | Template theo IFC revision, submit hợp lệ/sai phòng/sai quantity, stale revision, requirement ID ổn định và tải chưa biết. Store double. |
| IFC đọc native | Tầng, phòng, bbox mét từ IFC đơn vị mm, placement context, openings, adjacency, search, summary và thiết bị hiện hữu. Có model thật. |
| IfcMCP 10 queries | Chạy thư viện upstream thật trên IFC test: summary/tree/info/select/relations/contexts/materials/schema/clash/validate; kiểm tra allowlist. |
| Validation | Ngoài bbox phòng, orientation sai, proposal ID trùng và dữ liệu đầu vào. Không tuyên bố kiểm tra solid collision đầy đủ. |
| Draft/modify/remove/apply | Storage double + writer IFCOpenShell thật: tạo, sửa, xóa, ID ổn định, phiên bản cũ bị chặn, chưa duyệt không ghi, replay không trùng, validation giả bị từ chối, upload lỗi không đổi pointer. |
| Tính tải | Không cộng trùng thiết bị với requirement, PF có nguồn, tải thiếu nguồn/công suất giữ unknown. |
| Agent/Orchestrator | Tool-call schema, tool trái phép, giới hạn số lượt, state CAS, restart/checkpoint, đúng plan/version, bảng hỏi thay đổi chặn duyệt. Scripted LLM gọi runtime thật đến IFC output. |
| API | Upload/download IFC, file hỏng trả 422, token, project không tồn tại, REST agent/review và WebSocket events/input validation. |
| Tiện ích | Schema diagnostics, export giữ GlobalId kiến trúc, đường trực giao tham khảo và SVG escape. |

## Chưa xác minh bên ngoài

- Chưa chạy migration mới trên PostgreSQL/Supabase thật. Docker CLI có sẵn nhưng
  daemon không chạy. Các test writer dùng storage double, không chứng minh database
  trigger đã được cài lên server. Cần áp dụng migration trước khi vận hành.
- Chưa gọi model LLM thật: `.env` có khóa provider nhưng chưa có `LLM_MODEL`.
  Test agent dùng scripted LLM, không đánh giá chất lượng đề xuất của một model.
- Chưa chứng nhận tuân thủ tiêu chuẩn điện hoặc vị trí lắp đặt chính xác. Validator
  còn dựa trên bbox; cảnh báo hình học/rule chưa kiểm tra được giữ để kỹ sư review.
- Mười hai cảnh báo còn lại từ Supabase SDK về tham số HTTP deprecated; không phải
  lỗi assertion hoặc lỗi kết nối. Không sửa mã trong dependency bên ngoài.

`ctr.ifc` chỉ được đọc; mọi ghi thử thực hiện trên snapshot trong thư mục tạm.
