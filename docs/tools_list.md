# Danh sách tools đặt thiết bị

Cập nhật 17/11/2026: Agent tự đề xuất phương án và tọa độ; đã bỏ tool sinh phương án bằng thuật toán.
Các bảng A–D dưới đây mô tả nghiệp vụ. Hợp đồng gọi **thực tế** của Agent được xuất từ registry trong
[`tool-schemas.json`](tool-schemas.json), với mô tả và JSON Schema cho từng tham số.
Xem [`backend-pipeline.md`](backend-pipeline.md) để cài đặt, gọi API và đọc giới hạn kiểm tra hình học.

## Hợp đồng backend đang chạy

| Tool | Đầu vào Agent thực sự truyền | Ghi chú |
|---|---|---|
| `questionnaire_get_load_context` | `space_id?` | Backend gắn project/store/revision. |
| `ifc_get_placement_context` | `space_id` | Backend gắn đường dẫn model; bbox theo mét, không có polygon thật. |
| `engine_validate_placements` | `proposals, rules?` | Orientation yaw theo độ; kết quả sơ bộ complete=false. |
| `draft_save_placements` | `proposals` | Toàn bộ phương án; backend tự chạy validation, chọn plan/version và lưu checkpoint. |
| `draft_get_placements` | `offset?, limit?` | Đọc nháp theo trang, có total, UUID thiết bị và version. |
| `ifc_modify_device` | `device_id, changes` | Lưu revision nháp mới, chưa sửa IFC chính thức. |
| `ifc_remove_device` | `device_id, reason` | Lưu yêu cầu xóa, kiểm tra tham chiếu. |
| `calc_preliminary_load` | `scope_type, scope_id, snapshot` | Snapshot có load_id/room_id/load_class_id/kind và nguồn W/VA; giữ tải chưa biết. |
| `ifc_apply_placements_batch` | **Không cấp cho Agent** | Orchestrator gọi đúng phiên bản sau quyết định kỹ sư. |

Các tool tra Playbook giữ chữ ký như code trong `tools/ifc_read.py` và được re-export ở `tools/playbook.py`.
`project_standard` lưu ý phạm vi truy vấn; dữ liệu nguồn chưa khẳng định quy tắc đó phù hợp tiêu chuẩn đã chọn.
Định mức ổ cắm/CB không được dùng thay cho công suất tiêu thụ.

## Tools đọc bổ sung

Native IFC: `ifc_get_storeys`, `ifc_get_spaces`, `ifc_get_space_geometry`,
`ifc_get_space_adjacency`, `ifc_get_doors_windows`, `ifc_get_building_summary`,
`ifc_search_elements` (phân trang), `ifc_list_placed_devices`.

IfcMCP upstream (bật `ENABLE_IFCMCP=true` và cài extra): `ifc_summary`, `ifc_tree`,
`ifc_info`, `ifc_select`, `ifc_relations`, `ifc_contexts`, `ifc_materials`,
`ifc_schema`, `ifc_clash`, `ifc_validate`. Session riêng theo model; STEP ID chỉ
hợp lệ trong snapshot đó. Không cấp mutation/lifecycle tools cho Agent.

## Đặc tả nghiệp vụ tham chiếu

### Phase 1: Bảng hỏi nhu cầu (Questionnaire)

| # | Tool Name | Mô tả | Input | Output |
|---|-----------|--------|-------|--------|
| 8 | `questionnaire_get_template` | Lấy mẫu bảng hỏi theo loại công trình | `building_type` | `{questions: [{id, text, options, multi_select}]}` |
| 9 | `questionnaire_submit` | Lưu kết quả bảng hỏi | `{answers: [{q_id, selected}]}` | `{status, derived_load_context}` |
| 10 | `questionnaire_get_load_context` | Lấy context phụ tải đã tính từ bảng hỏi | — | `{hvac, water_heater, pump, ...per_space_loads}` |


## A. Nhận diện không gian và tra cứu dữ liệu

| # | Tool Name | Mô tả chức năng chi tiết | Input | Output |
|---|-----------|--------------------------|-------|--------|
| 1 | `ifc_get_placement_context` (Mới) | Đọc và phân tích không gian cần bố trí thiết bị từ IFC. Xác định loại phòng, diện tích, biên phòng, các đoạn tường và trần có thể lắp thiết bị, vị trí cửa, hướng mở cửa, cửa sổ, cột, nội thất và vật cản nếu IFC có dữ liệu. Đồng thời chuẩn hóa đơn vị, hệ tọa độ và cao độ để các tools sau có thể tính chính xác vị trí đặt. Nếu thiếu dữ liệu phải trả cảnh báo, không tự giả định phòng không có vật cản. | `space_id` | `{space_type, geometry, walls, ceiling, doors, windows, obstacles, coordinate_system, missing_data}` |
| 2 | `questionnaire_get_load_context` (Tái sử dụng Phase 1) | Lấy yêu cầu sử dụng điện mà người dùng đã cung cấp ở bảng hỏi: phòng nào cần điều hòa, bình nóng lạnh, bơm, thiết bị đặc biệt, nhu cầu ổ cắm và chiếu sáng. Những thông tin này giúp Agent xác định cần bố trí những loại điểm điện nào, tránh chỉ dựa vào diện tích hoặc tên phòng. | `space_id` (tùy chọn) | `{per_space_requirements, declared_loads, special_devices}` |
| 3 | `playbook_get_object_rules` (Nâng cấp) | Tra cứu các quy tắc thiết kế áp dụng cho từng loại phòng và thiết bị: số lượng tối thiểu hoặc cách tính số lượng, khoảng cách bố trí, chiều cao lắp đặt, vị trí ưu tiên, loại bề mặt lắp đặt (tường/trần), khoảng cách an toàn. Mỗi quy tắc cần có mã `rule_id`, nguồn và phạm vi áp dụng để hệ thống giải thích được vì sao chọn vị trí đó. | `device_type, space_type, project_standard` | `{rules: [{rule_id, quantity_rule, spacing, mounting_height, host_type, constraints, source}]}` |
| 4 | `playbook_query_devices` (Giữ) | Tra cứu catalog để chọn mẫu thiết bị phù hợp với yêu cầu thiết kế, ví dụ loại đèn, công tắc, ổ cắm hoặc điểm cấp điện. Cung cấp mã mẫu, thông số điện, công suất nếu có, loại IFC và thông tin hình học/ký hiệu tương ứng. Tool chỉ tra cứu mẫu, không trực tiếp tạo thiết bị trong IFC. | `category, filters` | `[{module_id, name, ifc_class, specs, rated_power, geometry_ref}]` |
| 5 | `playbook_get_load_class` (Nâng cấp) | Xác định một thiết bị hoặc điểm cấp điện thuộc nhóm phụ tải nào, chẳng hạn chiếu sáng, ổ cắm, điều hòa hay thiết bị công suất lớn. Kết quả giúp nhóm phụ tải cho bước tính toán và chia mạch sau này. Cần phân biệt phân loại phụ tải với hệ số nhu cầu và hệ số đồng thời, không mặc định chúng là một thuộc tính. | `space_type, device_category` | `{load_class_id, load_class_name, load_type}` |
| 6 | `playbook_get_terminal_class` (Chuyển từ Phase 4) | Xác định đặc điểm của điểm đấu nối điện cho thiết bị, ví dụ loại kết nối, điện áp, số pha và các thông số đầu nối cần thiết. Thông tin được gắn với điểm tải ngay từ Phase 2 để hỗ trợ chia mạch và nối dây ở các phase tiếp theo, chưa cần tạo đường dây hoặc mạch điện tại bước này. | `device_type, connection_requirements` | `{terminal_class_id, voltage, phases, connection_specs}` |

## B. Sinh phương án và kiểm tra

| # | Tool Name | Mô tả chức năng chi tiết | Input | Output |
|---|-----------|--------------------------|-------|--------|
| 7 | `ifc_list_placed_devices` (Giữ) | Lấy danh sách thiết bị đã tồn tại trong model hoặc phương án thiết kế. Giúp Agent biết phòng đã có những thiết bị nào, vị trí và mã thiết bị ra sao, từ đó tránh đặt trùng, hỗ trợ chỉnh sửa và so sánh với phương án mới. | `space_id / storey_id` | `[{element_id, device_type, space_id, position, properties}]` |
| 9 | `engine_validate_placements` (Mới – Quan trọng) | Kiểm tra phương án trước khi lưu chính thức, bao gồm cả vị trí Agent tự sinh và vị trí do kỹ sư chỉnh sửa. Kiểm tra thiết bị có nằm đúng phòng, đúng bề mặt, có đè lên cửa, cửa sổ, cột hoặc vật cản, có trùng thiết bị khác, và có phù hợp các quy tắc đã chọn hay không. Phân biệt lỗi bắt buộc sửa và cảnh báo cần kỹ sư đánh giá. | `proposals, placement_context, rules` | `{valid, errors: [], warnings: [], suggested_fixes: []}` |

## C. Lưu nháp, thực thi và chỉnh sửa IFC

| # | Tool Name | Mô tả chức năng chi tiết | Input | Output |
|---|-----------|--------------------------|-------|--------|
| 10 | `draft_save_placements` (Mới) | Lưu phương án bố trí vào Project DB dưới dạng bản nháp, chưa ghi chính thức vào IFC. Lưu danh sách thiết bị, vị trí, kết quả kiểm tra, cảnh báo và lý do đề xuất để kỹ sư xem xét. Có mã phương án và phiên bản để so sánh thay đổi, tránh mất kết quả khi người dùng sửa hoặc đóng phiên làm việc. | `project_id, proposals, validation_result` | `{plan_id, version, status: "pending_review", diff_summary}` |
| 11 | `ifc_apply_placements_batch` (Nâng cấp từ #14–15) | Sau khi kỹ sư duyệt, ghi toàn bộ thiết bị trong phiên bản phương án đã được duyệt vào IFC. Backend tự tạo hoặc tái sử dụng IFC Device Type khi cần, tạo các instance, gán tọa độ, hướng, hình học và thuộc tính. Phải tránh tạo đối tượng trùng khi chạy lại, duy trì ID của thiết bị không thay đổi, kiểm tra kết quả ghi và rollback nếu có lỗi để không làm hỏng phiên bản đã lưu trước đó. Chỉ Orchestrator được kích hoạt sau phê duyệt. | `approved_plan_id, version, project_id` | `{status, created_ids, updated_ids, removed_ids, errors, model_version}` |
| 12 | `ifc_modify_device` (Gộp và mở rộng) | Cho phép chỉnh sửa thiết bị đã đặt hoặc thiết bị đang nằm trong phương án nháp: di chuyển vị trí, thay đổi hướng, đổi tường/trần gắn thiết bị, đổi loại thiết bị hay cập nhật các thuộc tính. Các thay đổi phải được kiểm tra lại bằng `engine_validate_placements`. Thay đổi trên IFC chính thức phải đi qua cơ chế phê duyệt phù hợp. | `device_id, changes` | `{status, updated_device, validation_result}` |
| 13 | `ifc_remove_device` (Giữ) | Xóa một thiết bị không còn cần thiết khỏi phương án hoặc model sau khi được phép. Kiểm tra thiết bị có tồn tại và có đang được tham chiếu bởi đối tượng khác hay không; ghi nhận lịch sử xóa để hỗ trợ review và khôi phục phiên bản trước. | `device_id, reason` | `{status, removed_id, affected_references}` |

## D. Tính toán phụ tải sơ bộ

| # | Tool Name | Mô tả chức năng chi tiết | Input | Output |
|---|-----------|--------------------------|-------|--------|
| 14 | `calc_preliminary_load` (Gộp và nâng cấp từ #19–20) | Tổng hợp phụ tải điện sơ bộ dựa trên thiết bị và điểm tải được bố trí, kết hợp với yêu cầu ở bảng hỏi. Cho phép tính theo phòng, tầng hoặc zone để phục vụ Phase 3 – chọn tủ điện và lập sơ đồ đơn tuyến. Phân biệt công suất danh định, phụ tải kết nối và phụ tải tính toán; sử dụng hệ số phù hợp khi có cơ sở, không cộng trùng tải đã khai báo, không coi công suất chịu tải của ổ cắm là công suất tiêu thụ thực tế. | `scope_type, scope_id, plan_id, calculation_rules` | `{connected_load_W, estimated_demand_W, load_breakdown, assumptions, warnings}` |
