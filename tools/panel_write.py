"""Dev C: save/read/edit immutable panel drafts with injected specialist adapters."""

import base64
import json
import logging
import re
import xml.etree.ElementTree as ET
from functools import wraps
from typing import Literal
from uuid import UUID, uuid4

from database.panel_store import PanelConflict
from tools.panel_contracts import (
    PanelSchedule,
    SLDArtifact,
    SourceRefs,
    digest,
    json_copy,
    schedule_data,
)
from tools.panel_contracts import ScheduleOperation as ScheduleOperation
from tools.panel_validation import REFERENCE_ERRORS, panel_validate_schedule

log = logging.getLogger(__name__)
COLLECTION_KEYS = {
    "panels": "panel_id",
    "final_circuits": "circuit_id",
    "feeders": "feeder_id",
    "protection_groups": "protection_group_id",
    "phase_assignments": None,
}
PAGE_BUDGET = 5500  # Below the current providers' 6000-character result limit.
Section = Literal[
    "summary",
    "schedule",
    "validation_result",
    "calculation_summary",
    "diff_summary",
    "design_reasons",
    "edit_reason",
]


def entity_id(item, key):
    return item.get(key) if key else item.get("circuit_id") or item.get("feeder_id")


def schedule_diff(before, after):
    result = {}
    for collection, key in COLLECTION_KEYS.items():
        old = {entity_id(item, key): item for item in (before or {}).get(collection, [])}
        new = {entity_id(item, key): item for item in after[collection]}
        result[collection] = {
            "added": sorted(new.keys() - old.keys()),
            "removed": sorted(old.keys() - new.keys()),
            "updated": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
        }
    return result


def _uuid(value):
    return str(UUID(value)) if value is not None else None


def agent_summary(result):
    """Bound Agent output explicitly; full data remain available to backend/UI."""
    keys = (
        "status",
        "plan_id",
        "version",
        "review_status",
        "design_context_id",
        "schedule_hash",
        "calculation_id",
        "sld_status",
        "is_latest",
        "latest_version",
        "source_status",
        "recorded_can_approve",
        "can_approve",
        "replayed",
    )
    output = {key: result[key] for key in keys if key in result}
    validation = result.get("validation_result", result)
    summary = {
        key: validation[key]
        for key in ("valid", "complete", "can_approve", "approval_scope", "validated_schedule_hash")
        if key in validation
    }
    truncated = False
    for key in ("errors", "warnings", "unperformed_checks", "suggested_fixes", "deferred_checks"):
        entries = validation.get(key, [])
        summary[key + "_count"] = len(entries)
        # Explicit issue samples; never use these samples to decide completeness.
        samples = []
        for entry in entries[:1]:
            sample = {k: v[:160] if isinstance(v, str) else v for k, v in entry.items()}
            truncated |= sample != entry
            samples.append(sample)
        summary[key] = samples
        truncated |= len(entries) > len(samples)
    summary["details_truncated"] = truncated
    output["validation_result"] = summary
    # Keep validate's established top-level flags for callers.
    if "validation_result" not in result:
        output = summary
    if "schedule" in result:
        output["counts"] = {key: len(result["schedule"].get(key, [])) for key in COLLECTION_KEYS}
    output["detail_access"] = (
        "Saved: panel_get_schedule(section='validation_result'|'schedule', cursor=next_cursor). "
        "Unsaved: backend panel_validate_schedule returns full details; fix sampled issues and revalidate."
    )
    if len(json.dumps(output, ensure_ascii=False)) > PAGE_BUDGET:
        raise ValueError("Agent summary exceeds output budget")
    return output


def validate_svg(artifact, schedule):
    """Require usable, indexed, self-contained SVG before declaring a render ready."""
    svg = artifact["svg"]
    if "<!doctype" in svg.lower() or "<!entity" in svg.lower():
        raise ValueError("SVG declarations are not allowed")
    root = ET.fromstring(svg)
    if root.tag not in ("svg", "{http://www.w3.org/2000/svg}svg"):
        raise ValueError("Renderer must return an SVG root")
    allowed = {
        "svg",
        "g",
        "defs",
        "symbol",
        "use",
        "marker",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "title",
        "desc",
    }
    ids, drawable = set(), False
    for element in root.iter():
        tag = element.tag.removeprefix("{http://www.w3.org/2000/svg}")
        if tag not in allowed:
            raise ValueError("Unsupported SVG element")
        drawable |= tag in {
            "path",
            "rect",
            "circle",
            "ellipse",
            "line",
            "polyline",
            "polygon",
            "text",
        }
        for name, value in element.attrib.items():
            local = name.rsplit("}", 1)[-1].lower()
            urls = re.findall(r"url\s*\((.*?)\)", value, flags=re.IGNORECASE)
            if (
                local.startswith("on")
                or local in {"style", "src"}
                or (local == "href" and not value.startswith("#"))
                or any(not url.strip(" \t\r\n\"'").startswith("#") for url in urls)
            ):
                raise ValueError("SVG must be self-contained and inert")
        identity = element.get("id")
        if identity is not None:
            if not identity or identity in ids:
                raise ValueError("SVG IDs must be unique and nonempty")
            ids.add(identity)
    if not drawable or not set(artifact["element_index"]) <= ids:
        raise ValueError("SVG is empty or index points to missing SVG IDs")
    expected = {
        (kind, entity_id(item, COLLECTION_KEYS[group]))
        for kind, group in (
            ("panel", "panels"),
            ("circuit", "final_circuits"),
            ("feeder", "feeders"),
            ("protection_group", "protection_groups"),
        )
        for item in schedule.get(group, [])
    }
    indexed = {
        (item["entity_type"], item["entity_id"]) for item in artifact["element_index"].values()
    }
    if indexed != expected:
        raise ValueError("SLD index must cover exactly this schedule's entities")


class PanelService:
    """Backend binds project/context/store; only three read/validate/save methods
    enter ToolRegistry. Calculator, geometry_checker, rule_checker receive
    (context, canonical_schedule). Renderer receives the exact saved row.
    current_source_refs MUST read the authoritative current revisions, not echo
    the pinned snapshot. Callback results must include source_refs and hashes.
    """

    def __init__(
        self,
        store,
        design_context_id,
        *,
        current_source_refs,
        calculator=None,
        calculation_loader=None,
        geometry_checker=None,
        rule_checker=None,
        renderer=None,
    ):
        self.store, self.design_context_id = store, design_context_id
        self.current_source_refs = current_source_refs
        self.calculator, self.geometry_checker = calculator, geometry_checker
        self.calculation_loader = calculation_loader
        self.rule_checker, self.renderer = rule_checker, renderer

    def _context(self):
        context = self.store.load_context(self.design_context_id)
        if (
            context.project_id != self.store.project_id
            or context.design_context_id != self.design_context_id
        ):
            raise ValueError("Backend returned a foreign context")
        current = self.current_source_refs()
        current = current if isinstance(current, SourceRefs) else SourceRefs.model_validate(current)
        if current != context.source_refs:
            raise PanelConflict("Source revisions changed; create a new design context")
        return context

    def _evaluate(self, schedule, calculation_id=None):
        context = self._context()
        # A supplied ID refers to a stored calculation, not a freshly generated ID.
        if calculation_id is not None:
            if self.calculation_loader is None:
                raise ValueError("calculation_loader is required when calculation_id is provided")
            calculation = self.calculation_loader(context, calculation_id)
            if (
                not isinstance(calculation, dict)
                or calculation.get("calculation_id") != calculation_id
            ):
                raise PanelConflict("Unknown calculation_id in this design context")
        else:
            # Recompute after every save/edit; Agent never supplies computed values.
            calculation = self.calculator(context, json_copy(schedule)) if self.calculator else None
        geometry = (
            self.geometry_checker(context, json_copy(schedule)) if self.geometry_checker else None
        )
        rules = self.rule_checker(context, json_copy(schedule)) if self.rule_checker else None
        validation = panel_validate_schedule(
            schedule,
            context=context,
            calculation=calculation,
            geometry=geometry,
            rule_results=rules,
        )
        return context, calculation, validation

    def panel_validate_schedule(
        self,
        design_context_id: str,
        proposed_schedule: PanelSchedule,
        calculation_id: str | None = None,
    ) -> dict:
        """Kiểm định một phương án tủ điện đầy đủ; chỉ đọc, không lưu hoặc duyệt.

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
        """
        if design_context_id != self.design_context_id:
            raise ValueError("Context is not bound to this session")
        return self._evaluate(schedule_data(proposed_schedule), calculation_id)[2]

    def get_schedule_snapshot(self, plan_id: str | None = None, version: int | None = None) -> dict:
        """Backend/UI đọc toàn bộ nháp; không đăng ký cho Agent vì payload có thể lớn.

        INPUT: plan_id UUID tùy chọn, version số nguyên dương tùy chọn; version yêu cầu
        plan_id. Bỏ cả hai lấy mới nhất trong project bound. Không có nháp trả status=no_draft.
        OUTPUT: metadata, schedule, calculation_summary, validation_result đầy đủ,
        diff_summary, affected_entity_ids, design_reasons, edit_reason, sld_status;
        không chứa SVG. Kiểm tra nguồn và revision mới nhất để tính can_approve hiện tại;
        recorded_can_approve giữ kết quả lúc lưu. Không sửa snapshot trong DB.
        Dùng version trả về làm expected_version khi sửa. Agent phải dùng
        panel_get_schedule có phân trang thay vì expose hàm này vào tool registry.
        """
        if version is not None and (plan_id is None or type(version) is not int or version < 1):
            raise ValueError("version requires plan_id and must be a positive integer")
        row = self.store.get(_uuid(plan_id), version)
        if row is None:
            return {"status": "no_draft"}
        return self._public(row, include_schedule=True)

    def panel_get_schedule(
        self,
        plan_id: str | None = None,
        version: int | None = None,
        section: Section = "summary",
        cursor: str | None = None,
    ) -> dict:
        """Đọc phương án tủ đã lưu trong project được backend gắn sẵn; chỉ đọc.

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
        """
        if section not in (
            "summary",
            "schedule",
            "validation_result",
            "calculation_summary",
            "diff_summary",
            "design_reasons",
            "edit_reason",
        ):
            raise ValueError("Unknown section")
        offset, token = 0, None
        if cursor is not None:
            try:
                if len(cursor) > 2048:
                    raise ValueError("Cursor too long")
                token = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
                if (
                    not isinstance(token, dict)
                    or token["section"] != section
                    or token["project_id"] != self.store.project_id
                    or type(token["offset"]) is not int
                    or token["offset"] < 0
                    or type(token["version"]) is not int
                    or token["version"] < 1
                    or (plan_id is not None and _uuid(plan_id) != token["plan_id"])
                    or (version is not None and version != token["version"])
                ):
                    raise ValueError("Mismatched cursor")
                plan_id, version, offset = token["plan_id"], token["version"], token["offset"]
            except (ValueError, KeyError, TypeError, UnicodeError) as error:
                raise ValueError("Invalid cursor for this project/section/revision") from error
        snapshot = self.get_schedule_snapshot(plan_id, version)
        if snapshot.get("status") == "no_draft":
            if token:
                raise ValueError("Cursor revision no longer exists")
            return snapshot
        result = agent_summary(snapshot)
        if section == "summary":
            if cursor:
                raise ValueError("Summary is not paged")
            return result
        # Page the immutable stored section, not a validation flag that can change
        # between calls. Current eligibility lives in the outer response only.
        row = self.store.get(snapshot["plan_id"], snapshot["version"])
        value = row.get(section)
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        section_hash = digest(value)
        if token and (
            token.get("section_hash") != section_hash
            or token.get("schedule_hash") != snapshot["schedule_hash"]
        ):
            raise ValueError("Cursor content hash changed")
        if offset >= len(serialized):
            raise ValueError("Cursor offset outside section")
        # Summary issue lists are unnecessary in every data page.
        result.pop("validation_result", None)
        result.update(
            section=section,
            offset=offset,
            section_hash=section_hash,
            total_chars=len(serialized),
            encoding="json_text_fragments",
        )

        def page(end):
            next_cursor = None
            if end < len(serialized):
                payload = {
                    "project_id": self.store.project_id,
                    "plan_id": snapshot["plan_id"],
                    "version": snapshot["version"],
                    "schedule_hash": snapshot["schedule_hash"],
                    "section": section,
                    "section_hash": section_hash,
                    "offset": end,
                }
                next_cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
            return {**result, "json_fragment": serialized[offset:end], "next_cursor": next_cursor}

        low, high = offset, min(len(serialized), offset + PAGE_BUDGET)
        while low < high:
            middle = (low + high + 1) // 2
            if len(json.dumps(page(middle), ensure_ascii=False)) <= PAGE_BUDGET:
                low = middle
            else:
                high = middle - 1
        if low == offset:
            raise ValueError("Page metadata exceeds output budget")
        return page(low)

    def draft_save_panel_schedule(
        self,
        request_id: str,
        expected_version: int,
        proposed_schedule: PanelSchedule,
        design_reasons: list[str],
        plan_id: str | None = None,
    ) -> dict:
        """Lưu toàn bộ phương án tủ thành một revision nháp pending_review mới.

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
        """
        data = schedule_data(proposed_schedule)
        payload = {
            "kind": "save",
            "context": self.design_context_id,
            "plan_id": _uuid(plan_id),
            "expected_version": expected_version,
            "schedule": data,
            "reasons": design_reasons,
        }
        return self._save(request_id, payload, data, design_reasons, plan_id, expected_version)

    def _request(self, request_id, payload):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise ValueError("request_id must contain 1..128 characters")
        fingerprint = digest(payload)
        previous = self.store.request(request_id)
        if previous:
            if previous["request_hash"] != fingerprint:
                raise PanelConflict("request_id already used with a different payload")
            return fingerprint, previous
        return fingerprint, None

    def _save(
        self, request_id, payload, data, reasons, plan_id, expected_version, edit_reason=None
    ):
        if type(expected_version) is not int or not 0 <= expected_version < 2**63 - 1:
            raise ValueError("expected_version must be a nonnegative 64-bit integer")
        if (
            not isinstance(reasons, list)
            or len(reasons) > 100
            or any(not isinstance(r, str) or not r.strip() or len(r) > 2000 for r in reasons)
        ):
            raise ValueError("design_reasons must be a list of up to 100 nonempty reasons")
        plan_id = _uuid(plan_id)
        fingerprint, replay = self._request(request_id, payload)
        if replay:
            return {**self._public(replay), "replayed": True}
        latest = self.store.get()
        if (latest and (latest["version"] != expected_version or latest["plan_id"] != plan_id)) or (
            not latest and (expected_version != 0 or plan_id is not None)
        ):
            raise PanelConflict("Read current plan/version before saving")
        context, calculation, validation = self._evaluate(data)
        references = [i for i in validation["errors"] if i["code"] in REFERENCE_ERRORS]
        if references:
            raise ValueError(
                "Invalid schedule references: " + "; ".join(i["message"] for i in references)
            )
        # Detect source changes during expensive calculations; SQL also checks under lock.
        self._context()
        row = {
            "plan_id": plan_id or str(uuid4()),
            "design_context_id": self.design_context_id,
            "request_id": request_id,
            "request_hash": fingerprint,
            "schedule": data,
            "schedule_hash": digest(data),
            "source_refs": context.source_refs.model_dump(),
            "calculation_summary": calculation,
            "validation_result": validation,
            "design_reasons": reasons,
            "edit_reason": edit_reason,
            "diff_summary": schedule_diff(latest["schedule"] if latest else None, data),
        }
        saved = self.store.save(row, expected_version)
        if self.renderer:
            saved = self._render(saved)
        return self._public(saved)

    def _render(self, row):
        try:
            artifact = SLDArtifact.model_validate(self.renderer(json_copy(row))).model_dump()
            if any(
                artifact.get(key) != row[key] for key in ("plan_id", "version", "schedule_hash")
            ):
                raise ValueError("Renderer returned the wrong revision")
            validate_svg(artifact, row["schedule"])
        except Exception:
            # A persisted draft must survive rendering failure. Do not leak backend errors.
            log.exception("Panel SLD rendering failed for saved version %s", row["version"])
            status, artifact = "failed", None
        else:
            status = "ready"
        try:
            return self.store.save_render(
                row["plan_id"], row["version"], row["schedule_hash"], status, artifact
            )
        except Exception:
            log.exception("Could not persist panel SLD status")
            return {**row, "sld_status": "pending"}

    def retry_sld(self, plan_id: str, version: int):
        """Backend-only: thử render lại sơ đồ cho đúng revision đã lưu; không dành cho Agent.

        INPUT: plan_id UUID, version nguyên dương; cần renderer đã cấu hình.
        CÁCH CHẠY: đọc đúng row bất biến, gọi renderer và kiểm tra artifact đúng
        plan/version/hash, có SVG và index hợp lệ. Không tính lại hoặc thay schedule.
        OUTPUT: metadata/validation và sld_status của revision; không trả SVG.
        Render lỗi => failed, nháp vẫn tồn tại; lỗi ghi trạng thái => pending để đọc lại.
        Không tạo revision mới. SQL giữ artifact ready hợp lệ trước lượt render lỗi
        đồng thời. Thiếu renderer/version sai => ValueError; không có row => KeyError.
        Hàm không approve hoặc sửa IFC.
        """
        if self.renderer is None:
            raise ValueError("SLD renderer is not configured")
        if type(version) is not int or version < 1:
            raise ValueError("version must be positive")
        row = self.store.get(_uuid(plan_id), version)
        if row is None:
            raise KeyError(plan_id)
        return self._public(self._render(row))

    def panel_modify_schedule(
        self,
        plan_id: str,
        expected_version: int,
        request_id: str,
        operations: list[ScheduleOperation],
        reason: str | None = None,
    ) -> dict:
        """Backend review: sửa nháp bằng batch operations, KHÔNG đăng ký cho Agent.

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
        """
        if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
            raise ValueError("operations must contain 1..100 changes")
        if reason is not None and (
            not isinstance(reason, str) or not reason.strip() or len(reason) > 2000
        ):
            raise ValueError("reason must be null or a nonempty string up to 2000 characters")
        ops = [
            x.model_dump()
            if isinstance(x, ScheduleOperation)
            else ScheduleOperation.model_validate(x).model_dump()
            for x in operations
        ]
        plan_id = _uuid(plan_id)
        if plan_id is None:
            raise ValueError("plan_id is required")
        payload = {
            "kind": "modify",
            "context": self.design_context_id,
            "plan_id": plan_id,
            "expected_version": expected_version,
            "operations": ops,
            "reason": reason,
        }
        _, replay = self._request(request_id, payload)
        if replay:
            return {**self._public(replay), "replayed": True}
        row = self.store.get(plan_id)
        if not row or row["version"] != expected_version:
            raise PanelConflict("Read current revision before editing")
        data = json_copy(row["schedule"])
        for operation in ops:
            collection = operation["collection"]
            key, eid = COLLECTION_KEYS[collection], operation["entity_id"]
            items = {entity_id(x, key): x for x in data[collection]}
            op, value = operation["op"], operation["value"]
            if op == "add":
                if eid in items or not isinstance(value, dict) or entity_id(value, key) != eid:
                    raise ValueError("add requires a new ID and a full entity with that ID")
                items[eid] = value
            elif op == "update":
                if (
                    eid not in items
                    or not isinstance(value, dict)
                    or not value
                    or entity_id({**items[eid], **value}, key) != eid
                ):
                    raise ValueError("update requires an existing ID; IDs are immutable")
                items[eid] = {**items[eid], **value}
            else:
                if eid not in items or value is not None:
                    raise ValueError("remove requires an existing ID and no value")
                del items[eid]
            data[collection] = list(items.values())
        data = schedule_data(data)
        return self._save(
            request_id,
            payload,
            data,
            row["design_reasons"],
            plan_id,
            expected_version,
            edit_reason=reason,
        )

    def panel_modify_circuit(self, plan_id, expected_version, request_id, operations, reason=None):
        """Alias tương thích tên T11 cũ; chuyển nguyên input sang panel_modify_schedule.

        Chỉ dành cho backend review, không đăng ký Agent. Tham số plan_id,
        expected_version, request_id, operations, reason và output/lỗi/retry giống
        panel_modify_schedule; không giới hạn ở circuit, cũng hỗ trợ panel/feeder/nhóm/
        phase assignment. Tích hợp mới dùng tên panel_modify_schedule, không đăng ký
        cả hai tên như hai tool độc lập. Tạo revision nháp mới, không approve/ghi IFC.
        """
        return self.panel_modify_schedule(plan_id, expected_version, request_id, operations, reason)

    def _public(self, row, include_schedule=False):
        keys = (
            "plan_id",
            "version",
            "review_status",
            "source_refs",
            "validation_result",
            "diff_summary",
            "sld_status",
            "design_context_id",
            "schedule_hash",
        )
        result = {key: row[key] for key in keys}
        latest = self.store.get()
        result["is_latest"] = bool(
            latest and latest["plan_id"] == row["plan_id"] and latest["version"] == row["version"]
        )
        result["latest_version"] = latest["version"] if latest else None
        try:
            context = self._context()
            stale = (
                row["source_refs"] != context.source_refs.model_dump()
                or row["design_context_id"] != context.design_context_id
            )
            result["source_status"] = "stale" if stale else "current"
        except PanelConflict:
            result["source_status"] = "stale"
        except Exception:
            log.exception("Could not verify current panel sources")
            result["source_status"] = "unavailable"
        result["recorded_can_approve"] = row["validation_result"]["can_approve"]
        result["can_approve"] = bool(
            result["recorded_can_approve"]
            and result["is_latest"]
            and result["source_status"] == "current"
            and row["review_status"] == "pending_review"
        )
        result["validation_result"] = {
            **row["validation_result"],
            "can_approve": result["can_approve"],
            "recorded_can_approve": result["recorded_can_approve"],
        }
        result["calculation_id"] = (row.get("calculation_summary") or {}).get("calculation_id")
        result["affected_entity_ids"] = sorted(
            {eid for d in row["diff_summary"].values() for ids in d.values() for eid in ids}
        )
        if include_schedule:
            result.update(
                schedule=row["schedule"],
                calculation_summary=row.get("calculation_summary"),
                design_reasons=row["design_reasons"],
                edit_reason=row.get("edit_reason"),
            )
        return json_copy(result)


def register_panel_tools(registry, service: PanelService):
    """Add only Dev C agent capabilities to the existing ToolRegistry.

    Call from the future panel-phase registry, after binding a backend-verified
    context. Modification/render retry remain human/backend operations.
    """

    def compact(function):
        @wraps(function)
        def call(*args, **kwargs):
            return agent_summary(function(*args, **kwargs))

        return call

    registry.add(compact(service.panel_validate_schedule))
    registry.add(service.panel_get_schedule)
    registry.add(compact(service.draft_save_panel_schedule))
