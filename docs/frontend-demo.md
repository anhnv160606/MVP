# Giao diện demo đặt thiết bị

Frontend HTML/CSS/JavaScript thuần được đóng gói cùng backend. Không cần npm
install, React hoặc chạy thêm frontend server.

## Xem ngay khi chưa cấu hình backend

```powershell
Set-Location "D:\AI AGENT\MVP\MVP"
.venv\Scripts\python.exe -m electrical_design preview --port 8001
```

Mở **http://127.0.0.1:8001** rồi nhấn **Xem dữ liệu mẫu**. Chế độ này hiển thị
phòng, marker thiết bị và cảnh báo minh họa. Các nút lưu, gọi Agent, duyệt và tải
IFC bị vô hiệu hóa; không gọi Supabase hoặc LLM. Không cần `.env` để preview.

## Dùng dữ liệu thật

Sau khi cấu hình Secret/service_role Supabase key, LLM_MODEL và migrations:

```powershell
.venv\Scripts\python.exe -m electrical_design check-backend
.venv\Scripts\python.exe -m electrical_design serve --port 8000
```

Mở **http://127.0.0.1:8000**. Nếu đã chạy server từ trước khi thêm frontend,
dừng bằng Ctrl+C và khởi động lại.

1. Nếu có API_TOKEN, nhấn biểu tượng cấu hình ở góc trên bên phải và nhập
   **API_TOKEN ứng dụng**, không nhập Supabase key hoặc LLM key. Nhấn kết nối
   để kiểm tra các bảng/bucket. Token chỉ giữ trong bộ nhớ tab, không lưu vào
   localStorage hoặc đưa vào URL.
2. Nhập tên dự án, chọn IFC4 rồi **Upload & tạo dự án**. Có thể mở dự án cũ bằng
   project UUID; trình duyệt chỉ nhớ ID gần nhất, không tự upload lại.
3. Chọn phòng, khai báo mục cần bố trí, số lượng, công suất tiêu thụ nếu biết,
   nguồn công suất và gợi ý vị trí. Nhấn **Lưu nhu cầu sử dụng**. Chỉnh sửa ở các
   phòng khác được giữ trong form và lưu cùng revision. Những khai báo ngoài
   phần được sửa vẫn được bảo toàn. Revision conflict được báo để tải lại dự án.
4. Nhập yêu cầu rồi **Đề xuất phương án**. Nhật ký hiển thị tool_start,
   tool_result và thông điệp Agent qua WebSocket. Nút **Dừng** gửi cancel tới
   backend; tool đang lưu dữ liệu có thể cần hoàn tất trước khi hủy.
5. Xem bảng thiết bị, tọa độ X/Y/Z, sơ đồ chiếu bằng, JSON toàn phương án và
   cảnh báo. Nhấn vào marker hoặc dòng thiết bị để chọn. Thống kê thiết bị là
   theo phòng; lỗi/cảnh báo và phê duyệt áp dụng cho **toàn bộ phương án**.
6. Để chỉnh phương án, nhập phản hồi ở phần Agent và chạy lại. Agent được nhắc
   giữ nguyên thiết bị của các phòng khác và proposed_id khi lưu revision mới.
7. Sau khi kỹ sư kiểm tra, đánh dấu xác nhận đã xem phương án/cảnh báo và nhấn
   **Duyệt & ghi IFC**. Backend kiểm tra đúng plan/version và ghi model. Sau khi
   thành công, nút **Tải IFC** được bật.

## Giới hạn có chủ đích

- Sơ đồ 2D là bounding box phòng và tọa độ thiết bị, không phải mặt bằng kiến
  trúc hoặc IFC viewer 3D. Chưa thể hiện đầy đủ tường, cửa và vật cản; cần mở IFC
  bằng viewer chuyên dụng để kiểm tra thực tế.
- Không kéo-thả thiết bị trên canvas. Chỉnh vị trí qua phản hồi cho Agent.
- Form demo dùng bảng hỏi nhập tay. Dự án đã dùng checklist hoặc một mục có
  nhiều khai báo được giữ ở chế độ đọc cho phần đó, tránh làm mất dữ liệu nâng cao.
- Các lỗi thiếu bảng, bucket, khóa hoặc LLM được hiển thị từ backend. Giao diện
  không tự tạo Supabase resources hoặc bỏ qua bước kỹ sư duyệt.
- Browser WebSocket gửi API token qua subprotocol base64url; client Python vẫn
  có thể dùng X-API-Key. Truyền qua HTTPS/WSS khi triển khai ngoài localhost.

## Source và kiểm chứng

- `electrical_design/web/index.html`, `styles.css`, `app.js`: frontend.
- `api/frontend.py`: static assets, preview độc lập, API bbox theo project và
  diagnostics. Middleware vẫn bảo vệ toàn bộ dữ liệu project.
- `node --test tests/frontend.test.mjs`: kiểm tra bảo toàn bảng hỏi, công suất
  ổ cắm, phân loại thiết bị và chuyển đổi tọa độ SVG.
- `python -m pytest -q tests/test_frontend.py tests/test_agent_runtime.py`:
  static routes, auth, bbox API, preview không có write endpoint và WebSocket.

Kiểm tra trình duyệt trực tiếp chưa hoàn tất: công cụ trình duyệt đã bị từ chối
quyền mở địa chỉ localhost trong phiên làm việc. Không có screenshot/visual QA
được xác nhận. API và JavaScript đã được kiểm tra bằng bộ test tự động.
