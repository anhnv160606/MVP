# Project DB nhóm C trên Supabase

Backend dùng kết nối PostgreSQL của Supabase qua `psycopg`. Migration `20261111000002_placement_state.sql` tạo schema riêng `mvp_placement` cho bản nháp, lịch sử xóa, phê duyệt, phiên bản IFC và lượt áp dụng. Schema này dành cho backend và nằm ngoài các schema đã khai báo trong Supabase Data API.

## Cấu hình backend

1. Áp dụng migration trên Supabase môi trường cần chạy bằng quy trình migration của nhóm. Backend dùng PostgreSQL connection string từ Supabase Connect. Với kết nối từ xa, dùng TLS theo cấu hình của dự án. Nếu dùng database role riêng, cấp `USAGE` trên schema và quyền `SELECT, INSERT` trên các bảng của nhóm C cho role backend đó.
2. Cài dependency bằng `py -m pip install -r requirements-ifc.txt`. Đặt `MVP_DATABASE_URL` trong môi trường backend. Biến này là chuỗi kết nối PostgreSQL, khác với URL hoặc API key của Data API.
3. Đặt `MVP_IFC_SNAPSHOT_DIR` tới thư mục bền vững mà backend có thể đọc và ghi. Các tiến trình cùng phục vụ một dự án phải dùng chung thư mục lưu IFC. PostgreSQL giữ tên tệp, mã hash và phiên bản. Backup cần bao gồm cả PostgreSQL lẫn thư mục IFC. Cấu hình mặc định dùng schema `mvp_placement`. `MVP_POSTGRES_SCHEMA` cho phép dùng schema riêng khi test.

Mỗi thao tác ghi lấy khóa giao dịch theo dự án trước khi đọc phiên bản. Bản nháp mới và lịch sử xóa commit cùng nhau. Khi áp dụng, backend ghi và mở lại tệp IFC mới trước khi ghi nhận phiên bản và lượt áp dụng trong cùng giao dịch PostgreSQL. Nếu DB thất bại sau khi tạo file, phiên bản đang dùng vẫn giữ nguyên. Một file chưa được tham chiếu có thể còn trên ổ đĩa.

Migration tạo bảng mới. Nếu cần giữ bản nháp hoặc phiên bản IFC từ môi trường cũ, nhóm cần nhập dữ liệu và sao chép các tệp IFC tương ứng trước khi chuyển môi trường chạy.

## Kiểm thử

Trỏ `MVP_TEST_DATABASE_URL` đến PostgreSQL **dành riêng để test**. Test tự động tạo và xóa một schema riêng cho từng bài, nên role test cần quyền tạo schema. Với script chạy thủ công, áp dụng migration vào database thử trước. Ví dụ khi đã chạy Supabase local với cổng theo `supabase/config.toml`:

```powershell
$env:MVP_TEST_DATABASE_URL = 'postgresql://postgres:postgres@127.0.0.1:54322/postgres'
py -m unittest discover -s tests -v
py -u -m scripts.manual_remove_a "D:\AI_CONSTRUCTTION\A.ifc"
```

Nếu chưa đặt URL thử nghiệm, unittest báo các bài PostgreSQL là `skipped`. Chỉ tính là đã kiểm thử database khi các bài thực sự chạy.
