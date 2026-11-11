# Phase 2 — Xóa thiết bị

`ifc_remove_device(device_id, reason, ...)` tạo một phiên bản phương án bỏ đúng một thiết bị. Backend cung cấp `project_id`, `plan_id`, `expected_version`, validator, context và rules. Dùng `proposed_id` khi `target="draft"`, hoặc IFC GlobalId khi `target="ifc"`. Các thiết bị trong IFC phải thuộc phương án do Phase 2 quản lý.

## Nguyên lý

1. Tool xác nhận phương án, thiết bị và phiên bản gốc. Phiên bản đã cũ trả `conflict`, thiết bị không tồn tại trả `failed`. Lý do xóa là bắt buộc.
2. Nếu thiết bị đã có trong IFC, tool kiểm tra quan hệ đang trỏ đến nó, kể cả khi người dùng gọi bằng mã nháp. Các quan hệ do writer quản lý như type, phòng, property set và host được dọn lúc áp dụng. Quan hệ ngoài luồng như nhóm/mạch hoặc vị trí con làm yêu cầu trả `blocked`. Kết quả `affected_references` liệt kê lớp IFC, STEP ID, GlobalId nếu có và cờ `blocks_removal`. Writer kiểm tra lại ở thời điểm áp dụng để chặn cả tham chiếu phát sinh sau khi tạo nháp.
3. Tool gọi validator cho toàn bộ danh sách còn lại. Ví dụ, nếu bỏ đèn cuối cùng làm phòng không đủ số lượng, kết quả là `validation_failed`. Thiếu validator, context hoặc rules trả `validation_unavailable`.
4. Nếu đạt, SQLite lưu bản `pending_review` và lịch sử xóa trong cùng giao dịch. `removed_id` là mã thiết bị đã bỏ khỏi **bản nháp**. Lấy lý do xóa bằng `ProjectStore().get_placement_removal(project_id, plan_id, version)`. Phiên bản trước vẫn đọc được qua `get_placement_plan`. Để đưa thiết bị trở lại model hiện hành, lấy dữ liệu của thiết bị từ bản cũ, lưu thành một bản nháp mới và đi qua quy trình duyệt.
5. Khi kỹ sư duyệt, Orchestrator gọi `apply_approved_placement_plan`. Lúc đó IFC mới có phiên bản mới và thiết bị bị xóa xuất hiện trong `removed_ids`.

## Thử thủ công trong VS Code

Trong Terminal PowerShell, mở MVP trên nhánh `phase-2/placement-tools` và chạy:

```powershell
cd D:\AI_CONSTRUCTTION\MVP
py -u -m scripts.manual_remove_a "D:\AI_CONSTRUCTTION\A.ifc"
```

Script đã có sẵn trong repo. Nếu Python báo thiếu `ifcopenshell`, chạy `py -m pip install -r requirements-ifc.txt` rồi thử lại. Script dùng bản sao IFC và database thử riêng. Validator và người duyệt được giả lập. Validator mẫu yêu cầu giữ ít nhất một đèn trong Phòng khách tầng 1 của `A.ifc`. Khi validator thật sẵn sàng, backend truyền nó qua `validate_placements` với context và rules thực tế.

| Bài thử | Kết quả đạt |
| --- | --- |
| Xóa C khỏi nháp | `pending_review 2`. Bản 1 còn C và lịch sử bản 2 có lý do xóa. |
| Yêu cầu xóa A bằng GlobalId | `pending_review 3`. Áp dụng trước khi duyệt trả `failed` và IFC vẫn ở model version 1. |
| Duyệt và áp dụng | Model version 2 mất A, giữ B với cùng GlobalId. |
| Thử xóa B, đèn cuối cùng | `validation_failed`, có mã `minimum_lights`. Không tạo bản nháp mới. |
| Đối chiếu file | Script in `ĐẠT` và hai đường dẫn IFC để mở trong viewer. Hash của IFC gốc và bản model cũ giữ nguyên. |

Kiểm thử tự động cho trường hợp chặn do nhóm/mạch, xung đột phiên bản và rollback khi ghi lịch sử lỗi:

```powershell
py -m unittest discover -s tests -p test_ifc_remove_device.py -v
```
