# Phase 2 — Dev C: kiểm định, lưu và sửa phương án tủ điện

Cập nhật: **2026-11-25**, contract **v2**. Checkout: `D:\MVP\phase2-dev-c`.
Nhánh gốc: `phase-2-Thiết-kế-hệ-thống-tủ-điện`. Bản bàn giao Dev C để review và tích hợp; các giới hạn tích hợp được ghi ở mục 10 và 13.

## 1. Phạm vi và sáu lỗi đã sửa

Phần Dev C theo tài liệu 16 tool:

- T6 cũ / Tool 14: `panel_validate_schedule`.
- Tool 15: `panel_get_schedule`.
- T9 cũ / Tool 16: `draft_save_panel_schedule`.
- T11 cũ: backend `panel_modify_schedule`; giữ alias `panel_modify_circuit`.
- Storage, migration và hook API/registry cho review. Không có approve/apply IFC.

Sáu sửa đổi của lần review:

1. **Kết quả bị cắt ở 6.000 ký tự:** tool đọc dùng trang tối đa 5.500 ký tự JSON
   theo cách serialize của provider hiện tại. Đọc được cả entity đơn lẻ rất lớn.
   Cursor ghim project/plan/version/section/hash. Backend/UI có hàm đọc đầy đủ riêng.
2. **Chặn duyệt tủ vì chưa routing:** phân biệt `approval_scope=panel_design|final`.
   Chỉ hai check chưa thực hiện `cable_protection`, `voltage_drop` được hoãn theo
   chính sách backend. Check đã fail vẫn chặn; thiếu check khác cũng chặn.
3. **ID kết quả tính bị coi là cũ do calculator sinh ID mới:** có `calculation_id`
   thì gọi loader lấy đúng record đó. Bỏ ID thì tính mới. Mọi bằng chứng vẫn phải
   khớp project, context, source revisions và schedule hash.
4. **Cờ duyệt cũ khi đọc/retry:** mọi phản hồi lưu, sửa, retry, đọc và render lại
   đều kiểm tra nguồn hiện hành và revision mới nhất. Tách `recorded_can_approve`
   khỏi `can_approve` hiện tại. Không sửa validation lịch sử trong DB.
5. **Mất lý do thiết kế khi sửa:** giữ nguyên `design_reasons`; lý do của lượt sửa
   được lưu vào `edit_reason` ở revision mới, kể cả khi reason=null.
6. **SLD ready chỉ có metadata:** yêu cầu artifact có SVG thực và index hợp lệ,
   đúng plan/version/hash; parse SVG, kiểm tra ID và độ phủ entity. Render hỏng
   vẫn giữ nháp. SQL cũng chặn ready thiếu nội dung bắt buộc.

## 2. File và trách nhiệm

- `tools/panel_contracts.py`: schema dùng chung, chuẩn hóa JSON, hash, chuyển mẫu v1.
- `tools/panel_validation.py`: validator thuần; không gọi DB/LLM.
- `tools/panel_write.py`: service lưu/đọc/sửa, pagination, kiểm tra artifact, registry.
- `database/panel_store.py`: truy vấn theo project; ghi qua transaction RPC Supabase.
- `api/routes/panels.py`: API review, đọc đầy đủ cho UI.
- `api/app.py`: hook tùy chọn `panel_service_factory`; không thay luồng Phase 1.
- `supabase/migrations/20261124010000_panel_schedules.sql`: bảng/context/RPC ban đầu.
- `supabase/migrations/20261125010000_panel_contract_v2.sql`: migration bổ sung.
- `docs/phase2-contracts.schema.json`: JSON Schema sinh từ Pydantic; model ở
  `#/$defs/PanelSchedule`, `PanelDesignContext`, `CalculationResult`, `SLDArtifact`,
  `ScheduleOperation`, `SaveRequest`, `ModifyRequest`.
- `docs/phase2-contract-examples.json`: ví dụ input hợp schema, chủ động để thiếu
  dữ liệu tính thực; không có kết quả kiểm định pass giả.

File test và demo nằm ngoài checkout theo yêu cầu không đưa test vào phần bàn giao.
JSON Schema mô tả cấu trúc; kiểm tra liên kết ID, tính duy nhất và nghiệp vụ vẫn
phải chạy Pydantic/validator backend.

## 3. Luồng chạy

```text
Placement approved + applied, IFC điện ready
  -> Dev A gom đủ thiết bị, nguồn, catalog/rules và source_refs
  -> Backend đăng ký PanelDesignContext bất biến
  -> Agent chia tủ/mạch/feeder, dùng phase_assignments từ bước cân pha
  -> Dev B tính tải / kiểm tra vị trí / quy tắc theo cùng schedule_hash
  -> panel_validate_schedule
  -> draft_save_panel_schedule: tính lại, validate lại, transaction lưu pending_review
  -> renderer nhận đúng revision đã lưu
  -> UI đọc đầy đủ, kỹ sư sửa bằng panel_modify_schedule
  -> tạo revision mới, tính/validate lại; giữ nguyên revision cũ
  -> luồng phê duyệt của team kiểm tra lại nguồn + version tại thời điểm duyệt
```

`can_approve=true` là điều kiện kỹ thuật trong scope ghi kèm; không tự phê duyệt.
DB hiện chỉ có trạng thái `pending_review` cho bảng tủ. Chưa triển khai ghi tủ IFC.

## 4. Schedule v2 — hợp đồng dùng với Dev A/B

Các collection chuẩn của tài liệu 16 tool giữ nguyên tên:

```json
{
  "schema_version": 2,
  "panels": [{"panel_id": "P1", "name": "Tủ chính", "role": "main"}],
  "final_circuits": [{
    "circuit_id": "C1", "panel_id": "P1", "name": "Chiếu sáng",
    "device_ids": ["light-1"], "voltage_V": 230
  }],
  "feeders": [],
  "phase_assignments": [{"circuit_id": "C1", "phases": ["L1"]}],
  "protection_groups": []
}
```

Đây là nháp thiếu catalog/vị trí; ID thiết bị phải có trong context. 230 V chỉ là
ví dụ, backend không tự chọn điện áp. **Không gửi `phases` bên trong circuit/feeder.**

Chi tiết đã cụ thể hóa thêm so với tài liệu định hướng:

- Panel có `storey_id`, `cabinet_catalog_id`, `main_breaker_catalog_id`,
  `accessory_catalog_ids`, `location`; role `main|distribution`.
- Circuit có `breaker_catalog_id`; feeder có `feeder_id`, `upstream_panel_id`,
  `downstream_panel_id`, `voltage_V`, `breaker_catalog_id`.
- `phase_assignments`: mỗi item có đúng một `circuit_id` **hoặc** `feeder_id`,
  cùng `phases`. Không truyền cả hai, không lặp target. Pha chưa chọn được để
  thiếu assignment và báo check chưa thực hiện, không tự suy pha từ vị trí.
- `protection_groups` là phần mở rộng cho RCCB nhóm: `protection_group_id`,
  `panel_id`, `circuit_ids`, `catalog_id`. RCBO riêng đặt tại breaker của mạch.
  MVP chưa hỗ trợ cùng mạch vừa RCBO vừa RCCB nhóm.
- Location: `space_id`, `host_id`, `position_m:{x,y,z}`, `orientation:{yaw}`,
  `geometry_revision`, `candidate_id?`. Mét trong model_world, yaw độ quanh Z.

Giới hạn 100 tủ, 2.000 mạch, 100 feeder, 2.000 nhóm, 2.100 assignments.
ID tủ/mạch/feeder/nhóm không trùng trong toàn schedule, không phải IFC GlobalId.
Trường không biết, NaN/Infinity và sai kiểu bị từ chối.

**Cùng một cách hash cho mọi adapter:**

```python
canonical = schedule_data(proposed_schedule)
schedule_hash = digest(canonical)
```

Chuẩn hóa bổ sung default/null và sắp xếp entity/list ID. Không hash JSON thô.
Thay tên/vị trí/thiết bị/catalog/pha đều đổi hash. Đổi thứ tự trình bày không đổi hash.

## 5. Context Dev A và catalog

`PanelDesignContext` là snapshot backend, không nhận trực tiếp từ Agent:

- `schema_version=2`, `status`, `project_id`, `design_context_id`, `source_refs`.
- `devices[]`: `device_id`, `ifc_global_id?`, `requirement_id?`, `space_id?`,
  `storey_id?`, `position_m?`, `load_class_id?`, `terminal_class_id?`,
  `circuit_group?`, `requires_supply`, `input_W?`, `input_VA?`, `power_factor?`,
  `power_sources[]`, `connection_requirements`.
- `connection_requirements` dùng `phases:1|3|null`, `voltage_V:number|null`,
  `dedicated_circuit:bool`; có thể chứa khóa JSON chuyên môn khác để Dev B dùng.
- `supply_point_id?` là phần bổ sung để liên kết nhiều biểu diễn của cùng điểm tải;
  tránh gán/cộng trùng ổ cắm và thiết bị đã thay thế suất tải ổ cắm.
- `supply_context`: `phases:1|3|null`, `voltage_LN_V?`, `voltage_LL_V?`,
  `earthing_system?`, `entry_point?`. Thiếu dữ liệu không tự mặc định.
- `unresolved_requirements[]`, `unmapped_devices[]`, `warnings[]`, `missing_data[]`,
  `assumptions[]`, `next_cursor?`, `rules[]`.
- Phần backend bổ sung: `catalog[]`, `space_ids`, `storey_ids`,
  `inventory_complete`, `required_spare_percent`, `approval_policy`.

Catalog giữ cấu trúc tài liệu: `catalog_id`, `category`, `manufacturer?`, `model?`,
`electrical_specs`, `dimensions_m?`, `mounting_type?`, `module_width_mm?`,
`module_count?`, `source`, `verification_status`, `missing_data[]`.
`electrical_specs` cụ thể: `rated_current_A`, `rated_voltage_V`, `poles`,
`breaking_capacity_kA`, `residual_current_mA`, `available_modules` (đều nullable).
`dimensions_m={width,height,depth}`. Category `cabinet|mcb|mccb|rccb|rcbo|accessory`.
Verification `verified|reference|unverified`; dữ liệu chưa xác minh chặn đủ điều kiện duyệt.

Backend phải gom hết trang trước khi đăng ký context đầy đủ. `next_cursor` còn,
inventory chưa đầy đủ, unresolved/unmapped/missing data đều khiến kiểm định chưa đủ.
Không coi `requires_supply=false` là phụ tải.

`source_refs` gồm placement_plan_id, placement_version, source_file_id,
model_revision, questionnaire_revision, playbook_revision, ruleset_revision.
SQL kiểm tra placement approved/applied, IFC ready đúng SHA và questionnaire mới nhất.
Context ID bất biến; mỗi project chỉ có một context hiện hành. Đổi catalog/rules
phải cập nhật context/revision tương ứng. `current_source_refs()` phải đọc nguồn
thực, không trả snapshot cũ cố định. Catalog/rules ngoài DB chưa có transaction
phân tán với migration này; team tích hợp phải quản lý revision tại nguồn.

## 6. Adapter Dev B và kiểm định

Chữ ký các adapter:

```python
calculator(context, canonical_schedule) -> dict
calculation_loader(context, calculation_id) -> dict | None
geometry_checker(context, canonical_schedule) -> dict
rule_checker(context, canonical_schedule) -> dict
renderer(saved_revision) -> dict
```

Kết quả calculation/geometry/rules đều có envelope bắt buộc:
`project_id`, `design_context_id`, `source_refs`, `schedule_hash`.
Envelope là phần backend bổ sung để kiểm soát dữ liệu cũ.

Calculator theo tài liệu: `calculation_id`, `status`, `circuit_results[]`,
`feeder_results[]`, `panel_results[]`, `source_results`, `scenario_results[]`,
`coverage`, `missing_data[]`, `warnings[]`, `assumptions[]`.
Ba collection là **array**, mỗi item có đúng ID của loại tương ứng và:
`connected_load_W/VA`, `estimated_demand_W/VA`, `design_current_A_by_phase`,
`formula_basis`. Dòng phải có cho mọi pha cấp entity; pha không tải trả 0 rõ ràng.
`coverage.complete=true` chỉ khi Dev B đã tính đầy đủ; không suy 0 từ trường thiếu.

Phần bổ sung `engineering_checks` có các khóa cable_protection, breaking_capacity,
load_characteristics, residual_coordination, phase_balance, voltage_drop.
Mỗi check: `{"status":"pass|fail|unknown","message":"..."}`.

Geometry dùng `panels[panel_id]` với room_membership, host_surface, orientation,
collision, clearance, component_fit. Rule checker dùng `checks.project_rules`,
thêm `warnings:[{code,entity_id,message}]` cho advisory. Đây là các adapter backend,
không phải thay đổi ba array kết quả tính của Tool 9 trong tài liệu 16 tool.
Không chuyển kết quả bbox sơ bộ thành khẳng định kiểm tra va chạm chính xác.

`panel_validate_schedule(design_context_id, proposed_schedule, calculation_id?)`:

- Có calculation_id: loader lấy record đó; ID không có hoặc chưa cấu hình loader
  là lỗi rõ ràng. Sai hash/context/revision trả `STALE_EVIDENCE`, không đủ duyệt.
- Bỏ calculation_id: calculator tính mới nếu đã nối. Save/edit luôn tính lại.
- Thiếu adapter: trả unperformed_checks; adapter ném lỗi hệ thống/sai schema thì
  dừng trước lưu, không tạo bản tính thành công giả.

Validator kiểm tra gán tải đúng một mạch, tải riêng, topology một tủ chính không
chu trình, tham chiếu, pha/điện áp/số cực, catalog, nhóm bảo vệ, dòng định mức,
tổng module/dự phòng và tính đồng nhất của bằng chứng. Tính tải, kiểm tra cáp,
hình học chính xác, quy tắc chuyên môn vẫn thuộc adapter Dev A/B.

Output đầy đủ: `valid`, `complete`, `can_approve`, `approval_scope`, `errors[]`,
`warnings[]`, `unperformed_checks[]`, `deferred_checks[]`, `suggested_fixes[]`,
`validated_schedule_hash`. Mỗi issue có ID ổn định. Suggested fixes hiện là
hướng dẫn xem/sửa input, không tự chọn catalog hoặc tự tạo tọa độ.

`approval_policy` mặc định của phase này:

```json
{"stage":"panel_design","defer_to_routing":["cable_protection","voltage_drop"]}
```

Chỉ backend được đặt policy. Unknown/thiếu hai check này có `blocking=false`,
`required_at=routing`, `complete=false`; vẫn có thể đủ duyệt thiết kế tủ khi mọi
check bắt buộc khác đạt. Stage `final` không hoãn. Known fail luôn vào errors.
Thiếu cả calculation hoặc coverage/dòng không đầy đủ không được phép hoãn.
Cảnh báo advisory cần kỹ sư xử lý ở luồng duyệt của team.

## 7. Lưu, sửa, đọc và retry

`draft_save_panel_schedule(request_id, expected_version, proposed_schedule,
design_reasons, plan_id=None)` nhận **toàn bộ** schedule.

- Lần đầu plan_id=null, expected_version=0; lần sau dùng plan/version mới nhất.
- Request mới dùng ID mới; retry giữ ID và toàn payload. Dùng lại ID với payload
  khác hoặc dùng version cũ => conflict, không tự tăng version rồi ghi đè.
- Cho lưu nháp có lỗi thiết kế để giữ công việc; sai schema/tham chiếu/nguồn bị từ chối.
- SQL khóa project, kiểm tra version và nguồn trong transaction; một plan lineage/project.
- Output service đầy đủ có plan/version/hash, validation/calculation ID, diff,
  affected_entity_ids, sld_status, source_refs và các cờ eligibility hiện hành.

`panel_modify_schedule(plan_id, expected_version, request_id, operations, reason?)`
là backend review, không đưa vào Agent registry. Operations add/update/remove trên
panels, final_circuits, feeders, protection_groups, phase_assignments. Mỗi operation
có `entity_id`, `collection`, `op`, `value?`; add cần entity đầy đủ, update là patch,
remove không có value. ID bất biến. Với phase_assignments, entity_id là circuit/feeder ID.
Di chuyển tải phải sửa cả hai mạch trong cùng request. Xóa entity phải xử lý các
tham chiếu liên quan trong cùng batch. Tối đa 100 operations, tạo một revision mới.
Giữ `design_reasons`; lưu `reason` vào `edit_reason`. Full save vẫn có quyền thay
design_reasons bằng danh sách gửi mới vì đó là ý định thay toàn bộ phương án.

`get_schedule_snapshot(plan_id?, version?)` là backend/UI: trả đầy đủ schedule,
calculation_summary, validation_result, design_reasons, edit_reason, diff và metadata.
`panel_get_schedule(..., section="summary", cursor=None)` là Agent: summary mặc định;
section khác trả các mảnh JSON không mất dữ liệu:

```python
page = service.panel_get_schedule(section="schedule")
fragments = [page["json_fragment"]]
while page["next_cursor"]:
    page = service.panel_get_schedule(section="schedule", cursor=page["next_cursor"])
    fragments.append(page["json_fragment"])
schedule = json.loads("".join(fragments))
```

Sections: schedule, validation_result, calculation_summary, diff_summary,
design_reasons, edit_reason. Cursor tự ghim version; section phải giữ nguyên.
Không coi một fragment là full schedule rồi gửi save. Offset tính theo ký tự;
section_hash kiểm tra JSON đã ghép. SVG không đưa vào context Agent.

Registry chỉ đăng ký validate/get/save. Validate/save qua registry trả flags,
số lượng issue, một issue mẫu mỗi loại và `details_truncated` rõ ràng, dưới 5.500
ký tự. Chi tiết bản đã lưu đọc bằng section; bản chưa lưu lấy qua service backend
hoặc sửa issue mẫu rồi validate tiếp. Nếu provider đổi giới hạn xuống thấp hơn,
team phải đổi PAGE_BUDGET tương ứng.

Mọi phản hồi revision có `is_latest`, `latest_version`, `source_status`
(current/stale/unavailable), `recorded_can_approve`, `can_approve`.
Đọc lịch sử hoặc retry cũ vẫn trả đúng revision gốc nhưng không nhận quyền duyệt
hiện tại. Riêng nội dung section validation_result là **snapshot lịch sử** để
các trang không thay đổi giữa chừng; dùng can_approve ở ngoài trang cho hiện tại.
Cờ trong phản hồi đọc không thay thế kiểm tra nguồn/version nguyên tử lúc approve.

## 8. SLD

Renderer trả `artifact_id`, `plan_id`, `version`, `schedule_hash`, `svg`,
`element_index`, `render_status="ready"`, `warnings[]`.
Index được cụ thể hóa là map SVG ID -> `{entity_type, entity_id}`; entity_type
panel/circuit/feeder/protection_group. Bao phủ đúng mọi entity trong schedule.
SVG phải có phần tử đồ họa, root svg, ID duy nhất, index trỏ tới ID tồn tại.
Chỉ chấp nhận các thẻ hình/text/group/defs/symbol/use/marker cơ bản; không script,
foreignObject, style inline, event handler, DTD/entity hoặc nguồn ngoài.
Renderer cần xuất thuộc tính trình bày SVG trực tiếp, href chỉ nội bộ `#...`.

Không renderer => pending; render sai/lỗi => failed, nháp không mất.
`retry_sld(plan_id,version)` không tạo revision. SQL không cho lượt render thất
bại ghi đè artifact ready hợp lệ. Kiểm tra này xác nhận cấu trúc và liên kết;
chất lượng thể hiện sơ đồ kỹ thuật vẫn cần review renderer/UI thực tế.

## 9. Supabase và migration

Chạy các migration project/questionnaire/device placements có sẵn trước, rồi:

1. `20261124010000_panel_schedules.sql` — nếu chưa chạy.
2. `20261125010000_panel_contract_v2.sql` — bổ sung edit_reason, yêu cầu schema v2
   cho ghi mới, kiểm tra nội dung artifact và thay hai RPC save/render.

Không sửa/ghi đè migration đầu để tránh môi trường đã apply bị lệch. Migration
bổ sung giữ nguyên schedule/hash lịch sử; artifact ready cũ thiếu nội dung bắt
buộc được chuyển failed để retry. Không tự chuyển dữ liệu context/schedule v1.

Bản v1 đã lưu vẫn đọc được. Muốn chỉnh tiếp: backend tạo context v2 mới, chuyển
bản sao schedule bằng `upgrade_schedule_v1(old_schedule)` rồi full save thành
revision mới, dùng version hiện tại. Không đổi row/hash cũ. Không dùng mẫu v1
trực tiếp với tools v2.

RLS bật; public/anon/authenticated không được truy cập bảng/RPC mới. Service role
chỉ SELECT bảng, ghi qua RPC SECURITY DEFINER/search_path cố định. Key backend
không gửi vào Agent/browser. Factory phải xác thực quyền project trước khi bind store.

**Chưa apply hoặc test migration trên Supabase thật trong lần sửa này.** Parser
SQL chỉ xác nhận cú pháp, không xác nhận RLS/khóa/concurrency ở môi trường thật.

## 10. Các điểm cần nối trước khi ghép chạy thật

Factory tạo PanelService với store theo project, context hiện hành và các adapter
ở mục 6; `current_source_refs` đọc nguồn thật. Đăng ký ba tool bằng
`register_panel_tools(registry, service)` trong phase thiết kế tủ.

Router được mount khi truyền `panel_service_factory` vào create_app:

```text
GET  /projects/{project_id}/panels
POST /projects/{project_id}/panels/drafts
POST /projects/{project_id}/panels/{plan_id}/modify
POST /projects/{project_id}/panels/{plan_id}/versions/{version}/retry-sld
```

GET trả snapshot đầy đủ; conflict HTTP 409; dữ liệu sai HTTP 422 qua app hiện tại.
Chưa có endpoint approve/apply. CLI mặc định chưa truyền factory; Agent runtime
chưa tự đăng ký các tool này. Orchestrator Phase 2 và prompt panel chưa được nối;
team phải đồng bộ `projects.phase=REVIEW_PANELS` với SQLite ProjectMemory.
Không coi việc có các module này là toàn bộ phase đã chạy end-to-end.

## 11. Kết quả kiểm tra và chạy lại

Dùng môi trường uv sẵn có `D:\MVP\MVP\.venv`, không đổi pyproject/uv.lock,
không tạo môi trường chung. Trong PowerShell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'D:\MVP\MVP\.venv'
uv run --no-sync --project D:\MVP\phase2-dev-c python C:\Users\duyan\.gemini\antigravity\scratch\mep_device_agent\test_panel_regressions_v2.py
uv run --no-sync --project D:\MVP\phase2-dev-c python D:\MVP\phase2-demo\demo_panel_flow_v2.py
```

Đã chạy 67 unittest: 36 baseline chuyển contract và 31 regression/integration;
bao gồm sáu lỗi, cursor/tải lớn/JSON có Unicode và dấu nháy, API 200/409/422,
strict schema, retry, nguồn cũ, render lỗi và edit phase assignments. Ruff check
và format đạt. Parser đọc migration đầu 21 câu SQL/5 hàm, migration bổ sung
11 câu SQL/3 function definitions (gồm 1 SQL helper và 2 PL/pgSQL RPC).

Demo v2 chạy 11 bước, tạo 3 revision, xuất input/output vào `D:\MVP\phase2-demo\runs`.
v1 nháp chưa đủ bằng chứng -> v2 cố tình phân thiết bị trùng bị báo lỗi -> v3 sửa
trùng; thử version cũ, retry, ID ngoài context và đọc phân trang. Sample JSON v2
có tên riêng; giữ nguyên sample v1 và lịch sử demo cũ.

Tests dùng kho nhớ và bằng chứng chuyên môn giả lập; không chứng minh thiết kế
điện thực tế đạt. Demo không cấu hình adapter tính/hình học/rules nên luôn ghi
rõ chưa đủ duyệt. Chưa kiểm tra Supabase/IFC/LLM/network thật.

Trước tích hợp: chạy migration trên Supabase phát triển; thử hai client cùng
expected_version, retry sau mất mạng, nguồn đổi giữa tính và lưu, caller không có
quyền bị chặn, artifact lỗi/retry; rồi test toàn luồng với adapter Dev A/B thật.

## 12. Description thực tế trong code và Agent registry

Docstring là nguồn description mà ToolRegistry đọc bằng inspect.getdoc. Wrapper giữ docstring qua functools.wraps. Chỉ validate/get/save được expose; các hàm review vẫn backend-only. Description mô tả cả output rút gọn ở registry và output đầy đủ của service.

### panel_validate_schedule

Kiểm định một phương án tủ điện đầy đủ; chỉ đọc, không lưu hoặc duyệt.

KHI GỌI: sau khi tạo/chỉnh schedule hoặc nhận kết quả tính tải, trước khi đề nghị
lưu/review. Đây là kiểm định phương án, không phải tool tự chia mạch, chọn CB,
tính tọa độ hay tự chứng minh tuân thủ tất cả tiêu chuẩn điện.

INPUT:
- design_context_id: ID backend đã cấp cho phiên hiện tại; không tự tạo ID.
- proposed_schedule: toàn bộ PanelSchedule v2, không phải patch. Gồm panels,
  final_circuits, feeders, phase_assignments; protection_groups nếu có RCCB nhóm.
  Pha nằm ở phase_assignments, không nằm trong circuit/feeder. Dùng ID ổn định;
  device/catalog/space/storey phải thuộc context. Vị trí là model_world mét,
  orientation.yaw là độ quanh Z. Không gửi project_id, valid, dòng tính hoặc
  bằng chứng hình học tự suy diễn bên trong schedule.
- calculation_id: bỏ/null để backend gọi calculator nếu được cấu hình; chỉ gửi
  ID của kết quả tính đã tồn tại cho chính schedule này. Có ID thì loader tải
  record đó, không tính lại để sinh ID khác. Không tái dùng ID sau khi sửa schedule.

CÁCH CHẠY: backend lấy context và revision nguồn thật, chuẩn hóa/hash schedule,
kiểm tra phân tải, topology, tham chiếu, pha/điện áp/số cực, catalog, dòng và
module/dự phòng. Calculator, geometry và rules adapters cung cấp kiểm tra
chuyên môn; mọi kết quả phải khớp project/context/source_refs/schedule_hash.
Thiếu adapter/bằng chứng được ghi rõ, không tự chuyển thành pass.

OUTPUT QUA AGENT REGISTRY: valid, complete, can_approve, approval_scope,
validated_schedule_hash; errors, warnings, unperformed_checks, suggested_fixes,
deferred_checks là danh sách mẫu tối đa một issue mỗi loại; mỗi loại có
<tên>_count là tổng số issue. details_truncated=true nghĩa là còn nội dung chưa
hiển thị hoặc message đã rút gọn. detail_access hướng dẫn lấy chi tiết.
Gọi service trực tiếp từ backend trả danh sách đầy đủ, không có các trường đếm
của bản rút gọn. Mỗi issue có code/entity_id/message/issue_id.

DIỄN GIẢI: valid=true chỉ là không có lỗi đã phát hiện; complete=true nghĩa là
không còn check chưa thực hiện. can_approve chỉ áp dụng cho approval_scope,
không phải quyết định duyệt và không phải trạng thái SLD. Ở panel_design, hai
check chưa thực hiện cable_protection/voltage_drop có thể được backend hoãn
sang routing: complete=false nhưng can_approve=true. Known fail luôn chặn;
stage final không hoãn. Agent không được thay policy để né kiểm định.

BƯỚC TIẾP: sửa errors và kiểm định lại; unperformed_checks cần dữ liệu/adapter
hoặc kỹ sư bổ sung. Với kết quả bị rút gọn, không coi issue mẫu là toàn bộ lỗi.
Bản đã lưu: đọc section=validation_result của đúng version để xem snapshot
kiểm định lúc lưu. Bản chưa lưu: backend lấy kết quả đầy đủ hoặc sửa các issue
mẫu rồi validate tiếp. Check cảnh báo với kỹ sư trước khi yêu cầu duyệt.

LỖI: sai schema/ID context hoặc thiếu loader khi có calculation_id => ValueError;
nguồn thay đổi/ID tính không tìm được => conflict. Sai hash/revision bằng chứng
=> STALE_EVIDENCE trong errors. Không retry nguyên input khi nguồn đã thay đổi;
yêu cầu backend cấp context mới. Không ghi DB, tạo revision hay sửa IFC.

### panel_get_schedule

Đọc phương án tủ đã lưu trong project được backend gắn sẵn; chỉ đọc.

KHI GỌI: trước khi sửa/lưu thay thế, sau khi save, hoặc khi cần xem lại lịch sử.
Không dùng tool này để đọc phương án chưa lưu hoặc tự tạo context mới.

INPUT:
- plan_id: UUID plan đã nhận từ backend; bỏ để đọc plan hiện hành của project.
- version: số nguyên dương; truyền version phải có plan_id (hoặc cursor ghim
  sẵn cả hai). Bỏ version lấy revision mới nhất. Không tự tăng version.
- section: summary mặc định trả metadata, counts số entity và validation rút
  gọn; schedule trả toàn bộ cấu trúc thiết kế qua các mảnh JSON;
  validation_result trả kiểm định lúc lưu; calculation_summary trả bản tính;
  diff_summary trả các ID added/removed/updated theo collection;
  design_reasons trả lý do thiết kế; edit_reason trả lý do lượt sửa hoặc null.
- cursor: null ở trang đầu; các trang sau dùng nguyên next_cursor vừa nhận,
  giữ cùng section. Không tự sửa/giải mã/tạo cursor. Nếu truyền thêm plan/version,
  chúng phải khớp revision mà cursor đã ghim.

OUTPUT: không có nháp => {status:no_draft}. Summary có plan_id/version,
schedule_hash, design_context_id, review_status, calculation_id, sld_status,
is_latest/latest_version, source_status, recorded_can_approve, can_approve,
counts và validation_result rút gọn (issue mẫu, *_count, details_truncated).
Summary không chứa toàn bộ schedule và không có next_cursor để duyệt summary.

PAGING: section khác summary trả metadata và json_fragment, offset, total_chars,
section_hash, encoding=json_text_fragments, next_cursor. Mỗi phản hồi tối đa
5.500 ký tự JSON theo serializer hiện tại. Một entity có thể bị chia qua nhiều
trang; mỗi fragment riêng có thể KHÔNG phải JSON hợp lệ. Nối nguyên văn các
json_fragment đúng thứ tự offset, gọi tiếp đến next_cursor=null, rồi parse
JSON một lần. Không bỏ ký tự, không tự chèn dấu phẩy và tuyệt đối không lấy
một trang làm full schedule để save. Cursor giữ nguyên revision dù có edit mới.
section_hash là hash của nội dung section; không thay cho schedule_hash.

TRẠNG THÁI: recorded_can_approve là kết quả lịch sử, can_approve bên ngoài trang
là điều kiện hiện tại sau kiểm tra nguồn và revision mới nhất. source_status
stale/unavailable hoặc is_latest=false khiến can_approve=false. Riêng JSON trong
section validation_result giữ nguyên flags lịch sử để ghép trang nhất quán;
không dùng flag bên trong đó thay cho flag hiện tại ở ngoài trang. can_approve
không tự phê duyệt. sld_status=pending/failed không có nghĩa lưu nháp thất bại.

BƯỚC TIẾP: đọc đủ schedule của version mới nhất, giữ ID, sửa theo yêu cầu rồi
validate/save với expected_version đã đọc. Nếu nguồn stale, yêu cầu backend
làm mới context trước khi ghi. Nếu cần xem lịch sử chỉ đọc, không ghi đè bản mới.
Cursor sai scope/hash/section/offset => ValueError; bắt đầu lại đúng section,
không tự bỏ qua dữ liệu. Tool không trả SVG, không ghi DB hoặc IFC.

### draft_save_panel_schedule

Lưu toàn bộ phương án tủ thành một revision nháp pending_review mới.

KHI GỌI: khi cần giữ phương án để kỹ sư review, sau khi đã đọc nháp hiện hành
và kiểm định các thay đổi. Đây là hành động ghi nháp; không approve hoặc apply IFC.

INPUT:
- request_id: chuỗi 1..128 ký tự, duy nhất trong project cho một yêu cầu. Retry
  cùng yêu cầu phải giữ nguyên ID và toàn payload; sửa payload dùng ID mới.
- plan_id: null khi tạo đầu tiên; các lần sau dùng UUID plan backend đã trả.
- expected_version: 0 khi chưa có nháp; nếu đã có thì dùng version mới nhất
  vừa đọc bằng panel_get_schedule. Không dùng version cũ hoặc tự đoán version.
- proposed_schedule: TOÀN BỘ PanelSchedule v2 mong muốn, không phải patch,
  không phải fragment. Giữ ID của entity còn tồn tại. Entity bị bỏ khỏi danh sách
  sẽ không có trong revision mới. phase_assignments nằm ở root; tham chiếu tải,
  catalog và vị trí phải thuộc context. Không gửi cờ valid/can_approve tự tạo.
- design_reasons: danh sách lý do thiết kế cho toàn phương án; tối đa 100 chuỗi
  không rỗng, mỗi chuỗi <=2.000 ký tự. Có thể []. Full save thay danh sách này
  bằng input mới; nếu muốn giữ lý do cũ phải đọc và gửi lại nguyên nội dung.

CÁCH CHẠY: backend tự lấy context/nguồn, tính lại nếu có calculator, kiểm định
lại, kiểm tra nguồn lần nữa rồi RPC lưu nguyên tử với expected_version/request_id.
Không tin kết quả validate cũ do Agent gửi. Cho lưu nháp có lỗi thiết kế hoặc
thiếu bằng chứng để tiếp tục sửa; từ chối sai schema, tham chiếu không tồn tại,
nguồn cũ hoặc version conflict. Sau commit, renderer nếu có chạy trên đúng
revision vừa lưu. Render thất bại không làm mất nháp.

OUTPUT QUA REGISTRY: plan_id, version, review_status, design_context_id,
schedule_hash, calculation_id, sld_status; can_approve, recorded_can_approve,
is_latest/latest_version, source_status; validation_result gồm flags, issue
mẫu, *_count và details_truncated. detail_access hướng dẫn đọc chi tiết.
Backend gọi trực tiếp còn nhận source_refs, diff_summary, affected_entity_ids
và toàn bộ validation_result. Agent lấy diff qua panel_get_schedule với
section=diff_summary, và kiểm định qua section=validation_result đúng version.
Không giả định output chứa full schedule hoặc SVG.

RETRY/LỖI: retry thành công trả cùng plan/version và replayed=true, không thêm
revision. Retry bản cũ vẫn đọc lại điều kiện duyệt hiện tại, không khôi phục
quyền duyệt lịch sử. Cùng request_id nhưng payload khác => conflict. Version
conflict: đọc mới nhất, xem thay đổi của người khác rồi chuẩn bị yêu cầu mới;
không chỉ tăng expected_version để ghi đè. Nguồn đổi: backend cấp context mới.
Nếu mất mạng và chưa rõ đã lưu chưa, retry nguyên request_id/payload; không tạo
ID khác để thử vì có thể tạo revision trùng ý định. Sai schema/tham chiếu phải
sửa input. Lỗi adapter trước commit dừng lưu.

BƯỚC TIẾP: báo đúng plan/version đã lưu và các lỗi/check thiếu; can_approve=false
không có nghĩa save thất bại. sld_status=pending có thể chưa có renderer hoặc
chưa ghi được trạng thái render; failed cần backend retry_sld, không save lại
chỉ để tạo sơ đồ. Duyệt cuối cùng thuộc kỹ sư/luồng approve riêng, không tool này.

### panel_modify_schedule

Backend review: sửa nháp bằng batch operations, KHÔNG đăng ký cho Agent.

INPUT: plan_id là UUID hiện hành; expected_version là revision vừa đọc;
request_id nhận diện yêu cầu/retry; operations gồm 1..100 thao tác;
reason là null hoặc lý do sửa không rỗng <=2.000 ký tự.
Mỗi operation có op=add|update|remove, collection=panels|final_circuits|feeders|
protection_groups|phase_assignments, entity_id, value. add cần entity mới đầy đủ
với ID khớp; update là patch các trường của entity có sẵn, không đổi ID; remove
không có value. Update thay trường/list nguyên khối, không deep-merge từng item.
Với phase_assignments, entity_id là circuit_id hoặc feeder_id của assignment.

CÁCH CHẠY: áp dụng tuần tự trên bản sao đúng revision, chuẩn hóa và kiểm định
toàn phương án sau batch, tính lại qua adapters, rồi lưu một revision mới.
Di chuyển tải cần sửa cả hai mạch trong cùng batch; xóa tủ/mạch phải sửa các
feeder, nhóm bảo vệ, phase_assignments liên quan. Không tự cascade tham chiếu.
Schema/tham chiếu sai => không lưu một phần. Lỗi thiết kế có thể lưu pending_review.
Giữ nguyên design_reasons; reason của lượt sửa lưu riêng vào edit_reason.

OUTPUT: plan/version/hash mới, validation_result đầy đủ, diff_summary theo
collection, affected_entity_ids, source_refs, calculation_id, sld_status và
eligibility hiện tại. Revision cũ không đổi. Render lỗi vẫn giữ nháp.
Retry giữ nguyên request_id/payload => cùng revision, replayed=true. Conflict
version/nguồn => đọc lại hoặc tạo context mới, không ghi đè tự động.
Hàm này không approve, không ghi IFC và không phải capability của Agent hiện tại.

### get_schedule_snapshot

Backend/UI đọc toàn bộ nháp; không đăng ký cho Agent vì payload có thể lớn.

INPUT: plan_id UUID tùy chọn, version số nguyên dương tùy chọn; version yêu cầu
plan_id. Bỏ cả hai lấy mới nhất trong project bound. Không có nháp trả status=no_draft.
OUTPUT: metadata, schedule, calculation_summary, validation_result đầy đủ,
diff_summary, affected_entity_ids, design_reasons, edit_reason, sld_status;
không chứa SVG. Kiểm tra nguồn và revision mới nhất để tính can_approve hiện tại;
recorded_can_approve giữ kết quả lúc lưu. Không sửa snapshot trong DB.
Dùng version trả về làm expected_version khi sửa. Agent phải dùng
panel_get_schedule có phân trang thay vì expose hàm này vào tool registry.

### retry_sld

Backend-only: thử render lại sơ đồ cho đúng revision đã lưu; không dành cho Agent.

INPUT: plan_id UUID, version nguyên dương; cần renderer đã cấu hình.
CÁCH CHẠY: đọc đúng row bất biến, gọi renderer và kiểm tra artifact đúng
plan/version/hash, có SVG và index hợp lệ. Không tính lại hoặc thay schedule.
OUTPUT: metadata/validation và sld_status của revision; không trả SVG.
Render lỗi => failed, nháp vẫn tồn tại; lỗi ghi trạng thái => pending để đọc lại.
Không tạo revision mới. SQL giữ artifact ready hợp lệ trước lượt render lỗi
đồng thời. Thiếu renderer/version sai => ValueError; không có row => KeyError.
Hàm không approve hoặc sửa IFC.

### panel_modify_circuit

Alias tương thích tên T11 cũ; chuyển nguyên input sang panel_modify_schedule.

Chỉ dành cho backend review, không đăng ký Agent. Tham số plan_id,
expected_version, request_id, operations, reason và output/lỗi/retry giống
panel_modify_schedule; không giới hạn ở circuit, cũng hỗ trợ panel/feeder/nhóm/
phase assignment. Tích hợp mới dùng tên panel_modify_schedule, không đăng ký
cả hai tên như hai tool độc lập. Tạo revision nháp mới, không approve/ghi IFC.


## 13. Đối chiếu class và ranh giới tích hợp trước push (2026-11-25)

Đã fetch origin và đối chiếu checkout Dev C, nhánh chung
`phase-2-Thiết-kế-hệ-thống-tủ-điện` tại 32407f4 và nhánh
`phase-2-dev-a-load-topology-location`. Worktree đã fast-forward lên 32407f4,
giữ nguyên thay đổi Dev C chưa commit. Chưa merge nhánh riêng Dev A.

### Định nghĩa dùng chung

Không tìm thấy class PanelSchedule/PanelDesignContext/CalculationResult tương
đương trong các bản code đã kiểm tra. Schema Phase 2 hiện có một nguồn ở
`tools/panel_contracts.py`. Đã chuyển ScheduleOperation, SaveRequest và
ModifyRequest về cùng file này. API/tool import cùng class object; đường import
cũ vẫn re-export để không làm hỏng code gọi đã có. Không đổi JSON/schema/hash.

Dev A/B/C import trực tiếp từ nguồn chung:

```python
from tools.panel_contracts import (
    PanelSchedule, PanelDesignContext, CalculationResult,
    ScheduleOperation, SaveRequest, ModifyRequest, SLDArtifact,
    schedule_data, digest,
)
```

Không định nghĩa lại những model này trong module tính tải/topology hoặc API.
JSON Schema trong docs là bản xuất từ Pydantic, không phải định nghĩa thứ hai
được chỉnh tay. Khi sửa contract cần xuất lại và kiểm tra JSON mẫu.

### Class đã có và lý do không gộp sai chức năng

- IfcReader/SpatialIndex đọc IFC và lập chỉ mục hình học; không phải schema phương
  án tủ. Giữ nguyên để Dev A dùng khi xây context.
- DeviceSpec là dataclass đầu vào writer IFC (guid, ifc_class, tuple vị trí/kích
  thước); Device trong panel_contracts là điểm tải (device_id, công suất, yêu cầu
  cấp điện). Hai loại khác đơn vị nghiệp vụ; không cast/đổi tên tự động.
- ProjectSnapshot là snapshot tải sơ bộ theo phòng/tầng/zone, chứa SpatialIndex;
  PanelDesignContext là snapshot JSON có catalog, nguồn và revision phục vụ kiểm
  định tủ. Dev A tiếp tục dùng ProjectSnapshot ở bước tính sơ bộ; backend chuyển
  dữ liệu đã xác nhận sang PanelDesignContext, không lấy hai kiểu thay nhau.
- LoadClass/Playbook/ScopedLoads mô tả lớp tải và tổng hợp sơ bộ; không thay thế
  CalculationResult theo circuit/feeder/panel có schedule_hash.
- StrictModel của questionnaire nằm trong module Phase 1 và không có cấu hình
  allow_inf_nan=False của Contract. Không kéo cả nghiệp vụ questionnaire vào
  Phase 2 chỉ để dùng lại hai dòng cấu hình, cũng không đổi hành vi Phase 1.
- ToolRegistry, Settings, get_supabase_client đã được dùng lại; không tạo registry,
  client Supabase hoặc trình đọc IFC mới cho Dev C.
- PanelService/PanelStore không trùng QuestionnaireStore hoặc ProjectMemory:
  khác bảng, vòng đời revision và RPC. Các exception conflict có tên khác vì
  API xử lý theo nghiệp vụ; không gộp chỉ vì cùng kế thừa ValueError.

### Các khác biệt dữ liệu thực sự đã phát hiện ở code teammate

Không trùng class không đồng nghĩa output nối thẳng được. Những ranh giới sau
chưa có adapter sản xuất và KHÔNG được coi là đã tích hợp end-to-end:

1. Dev A topology trả proposed_storey_id, served_* và feeder.upstream_id; có cả
   feeder nguồn ngoài vào tủ chính. Schedule v2 dùng storey_id,
   upstream_panel_id và chỉ feeder giữa các tủ. Cần bước chuyển proposal được
   xác nhận: bổ sung tên/circuit/catalog, giữ metadata phục vụ ở context, và xử lý
   nguồn ngoài trong supply_context. Không đổi tên mọi upstream_id thành panel
   ID vì sẽ tạo tham chiếu nguồn ngoài không tồn tại trong panels.
2. Dev A load summary tổng hợp theo tầng/nhóm, có coverage theo phạm vi và chưa
   có circuit_results/feeder_results/panel_results theo schedule_hash. Không dùng
   summary này làm CalculationResult hoặc tự gán coverage.complete=true.
3. Dev A location trả candidate có face_id, local_axes, volume và hard_checks
   dạng passed. Schedule Location chỉ lưu vị trí/host/orientation/candidate và
   geometry_revision; giữ evidence đầy đủ ở backend. Không đổi toàn bộ passed
   thành các check pass của validator khi chưa xác nhận cùng hình học/hash.
4. SLD generator mới ở nhánh chung trả render_status=complete|partial,
   sld_data, nhiều entity_type (device/breaker/busbar/protection), context trong
   index và SVG có style. SLDArtifact Dev C hiện yêu cầu ready, index chặt chẽ
   cho panel/circuit/feeder/protection_group, không nhận style hoặc sld_data.
   Không nối trực tiếp renderer này vào PanelService: sẽ báo failed nhưng giữ
   nháp. Cần adapter/contract renderer được team chốt, giữ payload phục vụ viewer
   và cảnh báo; không ép partial thành ready hoặc bỏ dữ liệu để làm test xanh.

### Mức sẵn sàng bàn giao

Có thể push phần Dev C để review/tích hợp: model tập trung, import tương thích,
không ghi đè code teammate; registry vẫn chỉ expose validate/get/save.
Chưa tuyên bố toàn phase chạy thật. Còn kiểm thử Supabase, adapter Dev A/B/SLD,
factory và orchestrator. Những phần này đã được xác định cụ thể ở trên, không
phải xung đột class ngầm. Nhánh/code chưa được publish của teammate không nằm
trong phạm vi kiểm tra này.


Kiểm tra sau gom schema: 71/71 unittest qua (67 regression và 4 kiểm tra class identity, một định nghĩa duy nhất, JSON Schema không đổi, JSON mẫu hợp lệ). Nhánh Dev A đối chiếu tại d340c32. Test nằm ngoài repo: test_panel_contract_audit.py. Danh sách push vẫn 11 file Dev C đã nêu; không đưa test/demo/.env lên GitHub.
