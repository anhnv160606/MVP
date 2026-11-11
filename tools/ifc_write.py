"""Approval-gated IFC placement operations.

``model_adapter`` is an application-owned IFC writer implementing
    ``apply_batch(records, idempotency_key, removed_ids)``, ``modify_device`` and
``remove_device``. It must perform its own IFC transaction/rollback. This keeps
IfcOpenShell lifecycle and file persistence in the existing engine layer.
"""
from __future__ import annotations

from typing import Any


def draft_save_placements(project_id: str, proposals: list[dict[str, Any]], validation_result: dict[str, Any], store: Any,
                          operation_notes: dict[str, Any] | None = None) -> dict[str, Any]:
    if not project_id:
        raise ValueError("Cần project_id để lưu bản nháp.")
    if not validation_result.get("valid", False) or validation_result.get("errors"):
        raise ValueError("Không thể lưu bản nháp chưa vượt qua kiểm định; hãy sửa proposals trước.")
    plan = store.save_plan(project_id, proposals, validation_result, operation_notes=operation_notes)
    return {**plan, "status": "pending_review"}


def ifc_apply_placements_batch(approved_plan_id: str, version: int, project_id: str,
                               store: Any, model_adapter: Any, approval_status: str) -> dict[str, Any]:
    if approval_status != "approved":
        raise PermissionError("Chỉ Orchestrator sau khi kỹ sư phê duyệt mới được ghi IFC.")
    plan = store.get_plan(project_id, approved_plan_id)
    if not plan or plan["version"] != version:
        raise ValueError("Không tìm thấy đúng phương án/version được duyệt.")
    if plan["status"] != "approved":
        raise PermissionError(f"Phương án đang ở trạng thái {plan['status']}, chưa thể ghi IFC.")
    if not plan["validation_result"].get("valid", False):
        raise ValueError("Phương án chưa vượt qua engine_validate_placements.")
    if model_adapter is None or not callable(getattr(model_adapter, "apply_batch", None)):
        raise RuntimeError("Thiếu IFC model adapter; không ghi file.")
    key = f"{project_id}:{approved_plan_id}:{version}"
    previous = store.get_latest_applied_plan(project_id, before_version=version)
    previous_ids = {str(p.get("proposed_id")) for p in (previous or {}).get("proposals", []) if p.get("proposed_id")}
    current_ids = {str(p.get("proposed_id")) for p in plan["proposals"] if p.get("proposed_id")}
    removed = sorted(previous_ids-current_ids)
    # Adapter owns atomic write/rollback and must make the key idempotent.
    try:
        result = model_adapter.apply_batch(plan["proposals"], idempotency_key=key, removed_ids=removed)
    except Exception as exc:
        return {"status": "failed", "created_ids": [], "updated_ids": [], "removed_ids": [],
                "errors": [str(exc)], "model_version": None}
    if not isinstance(result, dict) or result.get("status") != "success":
        return {"status": "failed", "created_ids": [], "updated_ids": [], "removed_ids": [],
                "errors": (result or {}).get("errors", ["IFC adapter did not confirm success"]), "model_version": None}
    store.set_plan_status(project_id, approved_plan_id, "approved", "applied")
    return {"status": "success", "created_ids": result.get("created_ids", []), "updated_ids": result.get("updated_ids", []),
            "removed_ids": result.get("removed_ids", []), "errors": [], "model_version": result.get("model_version")}


def ifc_modify_device(device_id: str, changes: dict[str, Any], model_adapter: Any,
                      placement_context: dict[str, Any] | None = None, rules: list[dict[str, Any]] | None = None,
                      approved: bool = False, proposals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if proposals is not None:
        updated = [dict(p) for p in proposals]
        target = next((p for p in updated if p.get("proposed_id") == device_id), None)
        if target is None:
            raise KeyError(f"Không có đề xuất {device_id}.")
        target.update(changes)
        from .placement import engine_validate_placements
        validation = engine_validate_placements(updated, placement_context or {}, rules or [])
        return {"status": "draft_updated", "updated_device": target, "validation_result": validation}
    if not approved:
        raise PermissionError("Thay đổi thiết bị trên IFC chính thức cần phê duyệt.")
    if not callable(getattr(model_adapter, "modify_device", None)):
        raise RuntimeError("Thiếu IFC model adapter.")
    result = model_adapter.modify_device(device_id, changes)
    return {"status": "updated", "updated_device": result, "validation_result": None}


def ifc_remove_device(device_id: str, reason: str, model_adapter: Any,
                      approved: bool = False, draft_proposals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Cần ghi lý do xóa để lưu lịch sử review.")
    if draft_proposals is not None:
        kept = [p for p in draft_proposals if p.get("proposed_id") != device_id]
        if len(kept) == len(draft_proposals):
            raise KeyError(f"Không có đề xuất {device_id}.")
        return {"status": "draft_removed", "removed_id": device_id, "reason": reason, "affected_references": [], "proposals": kept}
    if not approved:
        raise PermissionError("Xóa thiết bị trên IFC chính thức cần phê duyệt.")
    if not callable(getattr(model_adapter, "remove_device", None)):
        raise RuntimeError("Thiếu IFC model adapter.")
    affected = model_adapter.remove_device(device_id, reason=reason)
    return {"status": "removed", "removed_id": device_id, "reason": reason, "affected_references": affected or []}
