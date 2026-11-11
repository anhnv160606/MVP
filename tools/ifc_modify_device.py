"""Stage and validate an edit to a Phase 2 electrical device."""

import json
import sqlite3
from copy import deepcopy

from database.placement_application import get_applied_placement_plan, validate_plan_key
from database.project_store import PlacementVersionConflict, ProjectStore, placement_diff
from tools.draft_save_placements import (
    draft_save_placements,
    validate_placement_result,
    validate_proposals,
)


_TYPE_FIELDS = {"ifc_class", "module_id", "dimensions_m", "geometry_ref", "specs", "rated_power"}
_EDITABLE_FIELDS = _TYPE_FIELDS | {
    "device_type", "position", "orientation", "space_id", "host_id", "rule_ids", "reason", "properties",
}


def _result(status, validation_result, *, updated_device=None, plan_id=None, version=None):
    return {
        "status": status,
        "updated_device": updated_device,
        "validation_result": validation_result,
        "plan_id": plan_id,
        "version": version,
    }


def _failure(status, code, message):
    return _result(status, {
        "valid": False,
        "errors": [{"code": code, "message": str(message)}],
        "warnings": [],
        "suggested_fixes": [],
    })


def _json_copy(value):
    return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))


def _merge_device(device, changes):
    updated = dict(device)
    if any(changes[key] != device.get(key) for key in ("device_type", "ifc_class") if key in changes):
        # Old catalog metadata cannot be carried to a different type by default.
        for key in _TYPE_FIELDS | {"rule_ids"}:
            updated.pop(key, None)
    if "space_id" in changes and changes["space_id"] != device["space_id"]:
        updated.pop("rule_ids", None)
        updated.pop("host_id", None)
    for key, value in changes.items():
        if key == "properties":
            old_properties = updated.get(key, {})
            if not isinstance(value, dict) or not isinstance(old_properties, dict):
                raise ValueError("properties must be an object")
            updated[key] = {**old_properties, **value}
        elif key in _TYPE_FIELDS and value is None:
            updated.pop(key, None)
        else:
            updated[key] = value
    return updated


def _find_applied_device(project_id, plan_id, device_id):
    from ifc_engine.writer import device_guid, device_spec, open_model, verify_batch

    proposals, path = get_applied_placement_plan(project_id, plan_id)
    for proposal in proposals:
        proposed_id = proposal["proposed_id"]
        if device_guid(project_id, plan_id, proposed_id) == device_id:
            spec = device_spec(project_id, plan_id, proposal)
            verify_batch(open_model(path), {proposed_id: spec}, ())
            return proposed_id
    raise ValueError("IFC GlobalId is not a managed device in this project's applied plan")


def ifc_modify_device(
    device_id: str,
    changes: dict,
    *,
    project_id: str,
    plan_id: str,
    expected_version: int,
    validate_placements=None,
    placement_context=None,
    rules=None,
    target: str = "draft",
) -> dict:
    """Edit a proposal by ID, or stage a managed IFC device edit by GlobalId.

    The trusted backend supplies the plan scope and engine_validate_placements
    callable. Successful edits append a pending_review snapshot.
    """
    try:
        validate_plan_key(project_id, plan_id, expected_version)
        if not isinstance(device_id, str) or not device_id.strip() or device_id != device_id.strip():
            raise ValueError("device_id must be a non-empty identifier")
        if target not in ("draft", "ifc"):
            raise ValueError("target must be 'draft' or 'ifc'")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a non-empty object")
        if changes.keys() - _EDITABLE_FIELDS:
            raise ValueError("changes contains an unsupported field or tries to change a device ID")
        changes = _json_copy(changes)
        draft = ProjectStore().get_placement_plan(project_id, plan_id)
        if draft is None:
            raise ValueError("Placement plan was not found in this project")
        if draft["version"] != expected_version:
            raise PlacementVersionConflict(
                f"Expected version {expected_version}, current version is {draft['version']}"
            )
        proposed_id = device_id if target == "draft" else _find_applied_device(project_id, plan_id, device_id)
        previous = draft["proposals"]
        candidate = list(previous)
        for index, proposal in enumerate(previous):
            if proposal["proposed_id"] == proposed_id:
                updated = _merge_device(proposal, changes)
                candidate[index] = updated
                break
        else:
            raise ValueError("Device was not found in the latest draft for this plan")
        validate_proposals(candidate)
        from ifc_engine.writer import device_spec

        device_spec(project_id, plan_id, updated)

        if not callable(validate_placements) or placement_context is None or rules is None:
            return _failure("validation_unavailable", "validator_required",
                            "Provide engine_validate_placements, placement_context and rules")
        try:
            validation = validate_placements(
                proposals=deepcopy(candidate),
                placement_context=placement_context,
                rules=rules,
            )
            validate_placement_result(validation)
            validation = _json_copy(validation)
        except Exception as error:
            return _failure("validation_unavailable", "validator_failed", error)
        if not validation["valid"]:
            return _result("validation_failed", validation, plan_id=plan_id, version=expected_version)
        if not placement_diff(previous, candidate)["updated"]:
            return _result("unchanged", validation, updated_device=updated,
                           plan_id=plan_id, version=expected_version)

        saved = draft_save_placements(
            project_id, candidate, validation, plan_id=plan_id, expected_version=expected_version,
        )
        return _result(saved["status"], validation, updated_device=updated,
                       plan_id=plan_id, version=saved["version"])
    except PlacementVersionConflict as error:
        return _failure("conflict", "version_conflict", error)
    except (ValueError, TypeError, OverflowError, ImportError, OSError, RuntimeError, sqlite3.Error) as error:
        return _failure("failed", "edit_failed", error)
