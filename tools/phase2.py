"""Phase 2 tool registry and dependency binding for the Agent loop.

The returned ``tools`` value follows OpenAI-compatible function-tool format;
``execute(name, arguments)`` is the sole dispatch point. Database clients,
IFC readers/writers and approval policy are injected by the API/Orchestrator.
"""
from __future__ import annotations

from typing import Any, Callable

from . import calculation, ifc_read, ifc_write, placement, playbook, questionnaire
from database.postgres import connect_from_env


def build_phase2_toolset(*, project_id: str, store: Any, supabase_client: Any,
                         ifc_model_provider: Callable[[], Any] | None = None,
                         model_adapter_provider: Callable[[], Any] | None = None,
                         object_rules_client_provider: Callable[[], Any] | None = None,
                         is_plan_approved: Callable[[str, int], bool] | None = None) -> dict[str, Any]:
    """Bind session-scoped dependencies without exposing credentials to the LLM."""
    def model():
        return ifc_model_provider() if ifc_model_provider else None

    specs = [
        ("ifc_get_placement_context", "Đọc geometry và vật cản có trong IFC; trả thiếu_data nếu không xác định được.", {"space_id": {"type": "string"}}, ["space_id"]),
        ("questionnaire_get_load_context", "Lấy yêu cầu tải người dùng đã khai báo.", {"space_id": {"type": "string"}}, []),
        ("playbook_get_object_rules", "Tra rule theo thiết bị/phòng; trả rule_id và nguồn khi dữ liệu có.", {"device_type": {"type": "string"}, "space_type": {"type": "string"}, "project_standard": {"type": "string"}}, ["device_type", "space_type", "project_standard"]),
        ("playbook_query_devices", "Tra catalog thiết bị; không tạo đối tượng IFC.", {"category": {"type": "string"}, "filters": {"type": "object"}}, ["category", "filters"]),
        ("playbook_get_load_class", "Phân loại tải, tách riêng phân loại với hệ số nhu cầu.", {"space_type": {"type": "string"}, "device_category": {"type": "string"}}, ["space_type", "device_category"]),
        ("playbook_get_terminal_class", "Tra lớp đầu cuối để lưu với điểm tải.", {"device_type": {"type": "string"}, "connection_requirements": {"type": "object"}}, ["device_type", "connection_requirements"]),
        ("ifc_list_placed_devices", "Liệt kê thiết bị đã có trong phương án/model.", {"space_id": {"type": "string"}, "storey_id": {"type": "string"}}, []),
        ("engine_plan_placements", "Tạo đề xuất có thể giải thích từ context, rule, catalog và nhu cầu; không tự chọn số lượng thiếu.", {"space_ids": {"type": "array", "items": {"type": "string"}}, "device_specs": {"type": "array", "items": {"type": "object"}}, "quantity_overrides": {"type": "object"}}, ["space_ids", "device_specs"]),
        ("engine_validate_placements", "Kiểm tra tọa độ và xung đột với hình học IFC/rule đã cung cấp.", {"proposals": {"type": "array", "items": {"type": "object"}}}, ["proposals"]),
        ("draft_save_placements", "Lưu phương án đã kiểm định để kỹ sư review; chưa ghi IFC.", {"proposals": {"type": "array", "items": {"type": "object"}}, "validation_result": {"type": "object"}}, ["proposals", "validation_result"]),
        ("ifc_apply_placements_batch", "Ghi IFC cho phiên bản được kỹ sư duyệt thông qua Orchestrator.", {"plan_id": {"type": "string"}, "version": {"type": "integer"}}, ["plan_id", "version"]),
        ("ifc_modify_device", "Tạo phiên bản nháp mới sau khi sửa một điểm tải trong plan/version hiện tại.", {"device_id": {"type": "string"}, "changes": {"type": "object"}, "plan_id": {"type": "string"}, "version": {"type": "integer"}}, ["device_id", "changes", "plan_id", "version"]),
        ("ifc_remove_device", "Tạo phiên bản nháp mới sau khi xóa một điểm tải khỏi plan/version hiện tại.", {"device_id": {"type": "string"}, "reason": {"type": "string"}, "plan_id": {"type": "string"}, "version": {"type": "integer"}}, ["device_id", "reason", "plan_id", "version"]),
        ("calc_preliminary_load", "Tổng hợp tải nối và tải tính toán sơ bộ, công khai giả định/thiếu dữ liệu.", {"scope_type": {"type": "string", "enum": ["space", "storey", "zone", "project"]}, "scope_id": {"type": "string"}, "plan_id": {"type": "string"}}, ["scope_type", "scope_id"]),
    ]
    tool_defs = [{"type": "function", "function": {"name": name, "description": description,
                   "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}
                 for name, description, properties, required in specs]

    def context_for(space_id: str) -> dict[str, Any]:
        return ifc_read.ifc_get_placement_context(space_id, model=model())

    def existing_devices() -> list[dict[str, Any]]:
        adapter = model_adapter_provider() if model_adapter_provider else None
        registered = adapter.list_devices() if callable(getattr(adapter, "list_devices", None)) else []
        return ifc_read.ifc_list_placed_devices(placements=registered, model=model())["devices"]

    def rules_for(
        proposals: list[dict[str, Any]],
        contexts: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result_rows: list[dict[str, Any]] = []
        seen: set[str | None] = set()
        for proposal in proposals:
            sid = str(proposal.get("space_id", ""))
            ctx = contexts.get(sid, {})
            result = playbook.playbook_get_object_rules(
                supabase_client,
                str(proposal.get("device_type", "")),
                str(ctx.get("space_type") or ""),
            )
            wanted = set(proposal.get("rule_ids") or [])
            for rule in result["rules"]:
                if wanted and rule.get("rule_id") not in wanted:
                    continue
                key = rule.get("rule_id")
                if key not in seen:
                    result_rows.append(rule)
                    seen.add(key)
        return result_rows

    def validate_proposals(
        proposals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        space_ids = {
            str(proposal["space_id"])
            for proposal in proposals
            if proposal.get("space_id") is not None
        }
        contexts = {sid: context_for(sid) for sid in space_ids}
        rules = rules_for(proposals, contexts)
        return placement.engine_validate_placements(
            proposals,
            contexts,
            rules,
            existing_devices=existing_devices(),
        )

    def execute(name: str, args: dict[str, Any]) -> Any:
        args = args or {}
        if name == "ifc_get_placement_context":
            return context_for(args["space_id"])
        if name == "questionnaire_get_load_context":
            result = questionnaire.questionnaire_get_load_context(project_id, args.get("space_id"), store=store)
            return {key: result[key] for key in (
                "per_space_requirements", "declared_loads", "special_devices"
            )}
        if name == "playbook_get_object_rules":
            rules_client = (object_rules_client_provider()
                            if object_rules_client_provider else connect_from_env())
            try:
                result = playbook.playbook_get_object_rules(
                    rules_client, args["device_type"], args["space_type"], args["project_standard"]
                )
            finally:
                close = getattr(rules_client, "close", None)
                if callable(close):
                    close()
            return {"rules": result["rules"]}
        if name == "playbook_query_devices":
            catalog_client = connect_from_env()
            try:
                result = playbook.playbook_query_devices(
                    catalog_client, args["category"], args["filters"]
                )
            finally:
                catalog_client.close()
            return [{key: device.get(key) for key in (
                "module_id", "name", "ifc_class", "specs", "rated_power", "geometry_ref"
            )} for device in result["devices"]]
        if name == "playbook_get_load_class":
            load_client = connect_from_env()
            try:
                result = playbook.playbook_get_load_class(
                    load_client, args["space_type"], args["device_category"]
                )
            finally:
                load_client.close()
            rows = result["load_classes"]
            exact = [row for row in rows if str(row.get("load_class_name") or "").casefold()
                     == args["device_category"].casefold()]
            row = exact[0] if len(exact) == 1 else rows[0] if len(rows) == 1 else {}
            return {key: row.get(key) for key in (
                "load_class_id", "load_class_name", "load_type"
            )}
        if name == "playbook_get_terminal_class":
            requirements = args["connection_requirements"]
            terminal_client = connect_from_env()
            try:
                result = playbook.playbook_get_terminal_class(
                    terminal_client, args["device_type"], requirements
                )
            finally:
                terminal_client.close()
            rows = result["terminal_classes"]
            exact = [row for row in rows if str(row.get("terminal_class_name") or "").casefold()
                     == args["device_type"].casefold()]
            row = exact[0] if len(exact) == 1 else rows[0] if len(rows) == 1 else {}
            return {
                "terminal_class_id": row.get("terminal_class_id"),
                "voltage": requirements.get("voltage_v", requirements.get("voltage")),
                "phases": row.get("phases", requirements.get("phases")),
                "connection_specs": row.get("connection_specs"),
            }
        if name == "ifc_list_placed_devices":
            devices = existing_devices()
            return ifc_read.ifc_list_placed_devices(args.get("space_id"), args.get("storey_id"), placements=devices)
        if name == "engine_plan_placements":
            contexts = {str(sid): context_for(str(sid)) for sid in args["space_ids"]}
            all_rules, specs_for_plan = [], args["device_specs"]
            for sid, ctx in contexts.items():
                for spec in specs_for_plan:
                    result = playbook.playbook_get_object_rules(supabase_client, spec.get("device_type", spec.get("category", "")), str(ctx.get("space_type") or ""))
                    for rule in result["rules"]:
                        rule.setdefault("device_type", spec.get("device_type", spec.get("category")))
                        rule.setdefault("space_type", ctx.get("space_type"))
                    all_rules.extend(result["rules"])
            overrides = args.get("quantity_overrides", {}) or {}
            for rule in all_rules:
                key = f"{rule.get('space_type')}:{rule.get('device_type')}"
                if key in overrides:
                    rule["quantity"] = overrides[key]
            existing = existing_devices()
            qctx = questionnaire.questionnaire_get_load_context(project_id, store=store)
            return placement.engine_plan_placements(args["space_ids"], contexts, all_rules, specs_for_plan, existing, qctx)
        if name == "engine_validate_placements":
            space_ids = {str(p.get("space_id")) for p in args["proposals"] if p.get("space_id")}
            contexts = {sid: context_for(sid) for sid in space_ids}
            rule_rows = rules_for(args["proposals"], contexts)
            return placement.engine_validate_placements(args["proposals"], contexts, rule_rows, existing_devices=existing_devices())
        if name == "draft_save_placements":
            supplied_validation = args["validation_result"]
            if not supplied_validation.get("valid") or supplied_validation.get("errors"):
                raise ValueError("validation_result đầu vào chưa hợp lệ.")
            checked = validate_proposals(args["proposals"])
            return ifc_write.draft_save_placements(project_id, args["proposals"], checked, store)
        if name == "ifc_apply_placements_batch":
            plan = store.get_plan(project_id, args["plan_id"])
            if not plan:
                raise KeyError("Không tìm thấy plan.")
            gate = bool(is_plan_approved and is_plan_approved(args["plan_id"], args["version"]))
            if not gate:
                raise PermissionError("Orchestrator chưa xác nhận kỹ sư duyệt plan/version này.")
            return ifc_write.ifc_apply_placements_batch(args["plan_id"], args["version"], project_id, store,
                                                        model_adapter_provider() if model_adapter_provider else None, "approved")
        if name == "ifc_modify_device":
            source = store.get_plan(project_id, args["plan_id"])
            if not source or source["version"] != args["version"]:
                raise ValueError("Không tìm thấy đúng plan/version.")
            updated = [dict(proposal) for proposal in source["proposals"]]
            target = next((proposal for proposal in updated
                           if proposal.get("proposed_id") == args["device_id"]
                           or proposal.get("element_id") == args["device_id"]), None)
            if target is None:
                raise KeyError("Thiết bị không có trong phương án.")
            immutable_fields = {"proposed_id", "element_id"}
            if immutable_fields.intersection(args["changes"]):
                raise ValueError("Không được thay đổi proposed_id hoặc element_id của thiết bị.")
            target.update(args["changes"])
            validation = validate_proposals(updated)
            saved = ifc_write.draft_save_placements(project_id, updated, validation, store)
            return {"status": "draft_updated", "new_plan": saved,
                    "updated_device": target, "validation_result": validation}
        if name == "ifc_remove_device":
            source = store.get_plan(project_id, args["plan_id"])
            if not source or source["version"] != args["version"]:
                raise ValueError("Không tìm thấy đúng plan/version.")
            result = ifc_write.ifc_remove_device(
                args["device_id"], args["reason"], None,
                draft_proposals=source["proposals"],
            )
            validation = validate_proposals(result["proposals"])
            saved = ifc_write.draft_save_placements(
                project_id, result["proposals"], validation, store,
                operation_notes={"removed_device": args["device_id"], "reason": args["reason"]},
            )
            return {**result, "new_plan": saved, "validation_result": validation}
        if name == "calc_preliminary_load":
            plan = store.get_plan(project_id, args.get("plan_id")) if args.get("plan_id") else None
            if args.get("plan_id") and plan is None:
                raise KeyError("Không tìm thấy phương án được yêu cầu.")
            devices = plan["proposals"] if plan else existing_devices()
            qctx = questionnaire.questionnaire_get_load_context(project_id, args.get("scope_id") if args["scope_type"] == "space" else None, store=store)
            summary = calculation.calc_preliminary_load(args["scope_type"], args["scope_id"], args.get("plan_id"), devices, qctx["declared_loads"])
            summary["warnings"] = list(dict.fromkeys(summary["warnings"] + qctx.get("warnings", [])))
            return summary
        raise KeyError(f"Unknown Phase 2 tool: {name}")

    return {"tools": tool_defs, "execute": execute, "tool_names": [name for name, *_ in specs]}
