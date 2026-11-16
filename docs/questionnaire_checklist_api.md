# Backend bảng tick thiết bị

Giao diện tham chiếu: “Bảng tick chọn thiết bị cần cấp điện.html”. Bản demo HTML dùng hợp đồng API dưới đây. Thao tác lưu ghi nhận bảng hỏi cho bước bố trí.

## Chạy và thử

Cấu hình Supabase Client và áp dụng migration theo [README](../README.md).

```sh
cd "/Users/macbook/Documents/CODE/MVP/MVP"
.venv/bin/python -B -m api.routes.questionnaire \
  --ifc /duong-dan/A.ifc --playbook /duong-dan/Playbook.xlsx --project-id checklist-demo --port 8000
```

Mở [bảng hỏi demo](http://127.0.0.1:8000/projects/checklist-demo/questionnaire/demo).
Trong mục Thiết bị tùy chỉnh, chọn nhóm để xem min/max. Có thể nhập công suất điện thực tế
ghi trên nhãn thiết bị, ví dụ 60 W. Hai giá trị được lưu riêng trong Playbook.
Bấm **Thêm vào Playbook và chọn**, sau đó **Lưu bảng hỏi** để lưu các lựa chọn theo phòng.

Để tự tick trong terminal khác:

```sh
.venv/bin/python -B -m tools.phase1.checklist_terminal --url http://127.0.0.1:8000 --project-id checklist-demo
```

Chọn **6. Lưu bảng hỏi và xuất JSON**. Terminal ghi
`phase1-<project-id>-rev-<revision>.json` vào thư mục hiện tại. Thêm
`--output-dir ~/Downloads` để ghi thẳng vào Downloads.

Trên web, bấm **Lưu bảng hỏi**, rồi **Tải JSON**. File chứa cùng dữ liệu đầu vào
Phase 2 mà API `/load-context` trả về cho phiên bản đã lưu.
Nếu truyền `space_id`, kết quả chỉ chứa lựa chọn, nhu cầu và cảnh báo áp dụng
cho phòng đó, cùng cảnh báo chung. Mở lại toàn bộ bảng hỏi hoặc xem nhu cầu
chưa gán phòng bằng cách bỏ bộ lọc `space_id`.


Để chạy nhanh ví dụ tạo thiết bị và lưu tự động:

```sh
.venv/bin/python -B -m tools.phase1.checklist_client --project-id checklist-demo
```

Script tạo một quạt hút bổ sung thuộc nhóm Động cơ gia dụng, tự nhận khoảng
20–2.200 W, nhận catalog mới, gửi lựa chọn theo gợi ý
và đọc lại revision. Chạy lại script không tạo trùng thiết bị. `/docs` có OpenAPI.
Dùng project mới khi thử checklist nếu project cũ có câu trả lời nhập tay.

## Luồng frontend

1. GET `/projects/{project_id}/questionnaire/checklist-template`.
2. Render `spaces` theo tầng, `room_groups`, cột điều hòa 0/1/2 và bình nóng lạnh.
   Mặc định lấy `saved_design_input`; nếu chưa có lấy `suggested_design_input`.
   Gợi ý không tạo nhu cầu trước khi submit. Cho phép sửa `room_type` nhận diện sai.
3. Render bếp, máy bơm, máy giặt/chọn phòng và tủ lạnh. Nhiều bếp được phân biệt bằng IFC ID.
4. Cuối form: tên thiết bị và nhóm phụ tải. Dùng `load_groups` trong template hoặc
   GET `/load-groups` để hiển thị dropdown và khoảng min/max tự động. POST `/custom-devices`.
5. GET template lại để lấy revision catalog mới; giữ các tick đang sửa trên client.
   Thêm ID nhận được vào `design_input.custom_devices` và chọn phòng/số lượng nếu cần.
6. POST `/checklist-submit` toàn bộ form; GET `/load-context` để đọc nhu cầu cho Phase 2.

Tạo thiết bị là thao tác lưu catalog riêng, có hiệu lực ngay kể cả nếu người dùng
chưa xác nhận bảng hỏi. Bỏ tick không xóa thiết bị khỏi Playbook. Backend không tự
thay đổi workbook Excel. Danh mục custom hiện thuộc từng project, chưa có xác thực
đa người dùng; chỉ chạy local ở 127.0.0.1. Cần thêm quyền truy cập trước khi public.

## Thiết bị tùy chỉnh

```json
{"request_id":"uuid-do-frontend-tao-mot-lan","name":"Quạt hút bổ sung","group_name":"Động cơ gia dụng","group_revision":"copy-tu-load-groups"}
```

`request_id` dùng lại khi retry. Cùng ID/cùng nội dung trả cùng thiết bị; cùng ID
khác nội dung trả 409. Phản hồi gồm `device.load_class_id` và `playbook_revision`.
Tên và nhóm lấy từ danh sách là đầu vào chính. Frontend tự gửi `group_revision`
để kiểm tra phiên bản, người dùng không nhập hash. API vẫn hỗ trợ `input_W` tùy chọn
khi có công suất thực tế (tương thích client cũ); không dùng min/max làm input_W.
PF, VA và hệ số nhu cầu không tự mặc định. Nguồn `user_declared`, có group_snapshot
truy nguyên khoảng tham khảo.

Catalog gốc đọc từ workbook. Supabase Client lưu thiết bị tùy chỉnh trong
`phase1_custom_devices` theo project. Revision bảng hỏi giữ snapshot thông số
catalog để các lần bổ sung thiết bị giữ nguyên dữ liệu lịch sử.

## Gửi bảng tick

Các trường metadata lấy nguyên từ template. Ví dụ phần `design_input`:

```json
{
  "rooms": [
    {"space_id":"IFC_BEDROOM_ID","air_conditioners":1,"water_heater":false,"room_type":"bedroom"},
    {"space_id":"IFC_WC_ID","air_conditioners":0,"water_heater":true,"room_type":"bathroom"}
  ],
  "shared_water_heaters_by_storey":true,
  "kitchens":[],
  "pump":true,
  "washing_machine_space_id":null,
  "custom_devices":[{"load_class_id":"CUSTOM-ID-TRA-TU-API","space_id":"IFC_BEDROOM_ID","quantity":1}]
}
```

`rooms` phải chứa mọi phòng IFC đúng một lần. Không gửi tổng máy/tổng bình từ UI;
backend tự suy ra. 0/false là đã bỏ tick; omitted kitchen/custom means no selection.
Gửi full snapshot để giữ lựa chọn ở các phòng khác. Sửa/clear giữ ID của nhu cầu
còn tồn tại; revision cũ vẫn đọc được. Gửi stale revision trả 409. IFC thay đổi
trả 409; catalog/template đổi trả 422 và yêu cầu tải template mới.

Bếp: `space_id`, `stove: gas|electric`, `burners: single|double|null`, `oven`, `fridge`,
`dishwasher_catalog_id`, `microwave_catalog_id`. Gas yêu cầu burners=null, oven=false
để khớp mẫu; ổ cắm bếp vẫn có yêu cầu bố trí. Một bếp đôi là một thiết bị, không nhân
công suất hai lần. Máy rửa bát/lò vi sóng chưa có mục gốc: tạo custom rồi gửi ID.

Dùng chung nước nóng: một requirement/tầng, quantity=1, served_space_ids chứa các
WC đã tick; vị trí bình chưa được chọn, nằm trong unresolved_requirements. Máy bơm
cũng là nhu cầu toàn công trình, kèm ưu tiên tầng thấp nhất/tường ngoài. Phase 2
phải xác định vị trí từ hình học, không suy tọa độ từ tên tầng.

## Điện và đầu ra Phase 2 / #14

Thiết bị gốc lấy ID, khoảng W, hệ số và nguồn từ workbook. Workbook chỉ có khoảng
tham khảo, không có model máy cụ thể: không chọn max/trung bình làm công suất thực.
`input_W=null` khi chỉ có khoảng; nguyên khoảng nằm trong `catalog_snapshot`.
Thiết bị custom có `input_W` bằng số khai báo nếu cung cấp; khi chỉ chọn nhóm thì
`input_W=null`, khoảng min/max lưu trong `catalog_snapshot`.

`automatic_requirements` là yêu cầu bố trí chiếu sáng/công tắc/ổ cắm và quạt hút WC;
quantity=null, không tạo thiết bị hoặc cộng tải tại Phase 1. Ổ cắm không tự trở thành
tải tiêu thụ; công tắc không phải tải riêng. Lớp ổ cắm chưa ánh xạ được giữ null.

`requirements` và `unresolved_requirements` chứa ID ổn định, scope, space_id,
load_class_id, quantity, catalog_snapshot. Phase 2 hoàn thiện số lượng/vị trí,
chọn công suất/model rồi tạo placement. Adapter sang #14 đổi space_id→room_id,
truyền requirement_id, load_class_id, quantity và các trường công suất/nguồn.
Dùng `calculation_playbook(catalog)` để #14 nhận cả custom classes từ database;
CLI chỉ đọc Excel cũ không tự biết custom ID. Custom chưa có hệ số nhu cầu vẫn tính
được tải kết nối W; demand/VA chưa đủ dữ liệu trả null cùng cảnh báo.

## Quy tắc min/max theo nhóm

Nguồn chỉ là các lớp tải gốc có `source.kind=playbook`; dòng custom không làm thay
đổi khoảng nhóm của lần tạo tiếp theo. `min = min(các min có dữ liệu)`,
`max = max(các max có dữ liệu)`. Nhóm hoàn toàn thiếu dữ liệu trả null; thiếu một
phần trả `range_complete=false`. `source_load_classes` giữ ID, khoảng và nguồn
của từng dòng tham gia; `group_revision` đổi khi dữ liệu nguồn đổi. Nhóm không
có trong danh mục trả 422, hash cũ trả 409. Client không được gửi min/max tự sửa.

Đây là khoảng bao phủ tham khảo của nhóm, có thể rộng: Gia nhiệt 1.200–7.400 W,
Động cơ gia dụng 20–2.200 W. Không khẳng định mọi thiết bị của nhóm đều có công suất
nằm trong khoảng đó. Các nhóm ổ cắm giữ intent=supply_point, nguồn phát giữ
generation; không biến chúng thành thiết bị tiêu thụ. Chỉ equipment được gửi W
đầu vào thực tế. Không chỉnh workbook Excel; dữ liệu mới ghi PostgreSQL/Supabase.
