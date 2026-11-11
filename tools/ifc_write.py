"""Phase 2 tools for placement drafts, approved IFC writes, edits and removals."""

import json
import math
from copy import deepcopy

from database.placement_application import (
    PlacementApplicationError,
    application_transaction,
    get_applied_placement_plan,
    get_model_row,
    has_application,
    latest_applied_version,
    latest_approved_version,
    load_plan,
    publish_model,
    record_application,
    validate_plan_key,
    verified_model_path,
)
from database.postgres import ProjectDatabaseError
from database.project_store import (
    PlacementPlanNotFound,
    PlacementVersionConflict,
    ProjectStore,
    placement_diff,
)


__all__ = [
    "draft_save_placements",
    "ifc_apply_placements_batch",
    "ifc_modify_device",
    "ifc_remove_device",
]


_TYPE_FIELDS = {"ifc_class", "module_id", "dimensions_m", "geometry_ref", "specs", "rated_power"}
_EDITABLE_FIELDS = _TYPE_FIELDS | {
    "device_type", "position", "orientation", "space_id", "host_id", "rule_ids", "reason", "properties",
}


def draft_save_placements(
    project_id: str,
    proposals: list[dict],
    validation_result: dict,
    *,
    plan_id: str | None = None,
    expected_version: int | None = None,
) -> dict:
    """Save a complete draft snapshot. Supply both optional fields to revise a plan.

    Failed placement checks are saved for review along with warnings, suggested
    fixes, and any reasoning carried in the proposal or validation payload.
    An empty proposals list represents a snapshot with no devices.
    """
    _require_identifier(project_id, "project_id")
    if plan_id is None:
        if expected_version is not None:
            raise ValueError("expected_version requires plan_id")
    else:
        _require_identifier(plan_id, "plan_id")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("Revising a plan requires a positive integer expected_version")
    _validate_proposals(proposals)
    _validate_placement_result(validation_result)

    return ProjectStore().save_placement_plan(
        project_id, proposals, validation_result,
        plan_id=plan_id, expected_version=expected_version,
    )


def ifc_apply_placements_batch(approved_plan_id: str, version: int, project_id: str) -> dict:
    """Apply an approved revision and return changed IFC GlobalIds and the model version.

    Only the Orchestrator calls this tool, after the backend records approval.
    """
    try:
        validate_plan_key(project_id, approved_plan_id, version)
        with application_transaction(project_id) as (connection, snapshot_dir):
            return _apply_approved_batch(connection, snapshot_dir, project_id, approved_plan_id, version)
    except (PlacementApplicationError, ImportError, OSError, RuntimeError, ProjectDatabaseError) as error:
        return _batch_result("failed", errors=[str(error)])


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

    The backend supplies the plan scope and engine_validate_placements callable.
    A successful change returns the edited proposal and a pending_review revision.
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
        _validate_proposals(candidate)
        from ifc_engine.writer import device_spec

        device_spec(project_id, plan_id, updated)

        validation = _run_placement_validator(
            candidate, validate_placements=validate_placements, placement_context=placement_context, rules=rules,
        )
        if not validation["valid"]:
            return _modify_result("validation_failed", validation, plan_id=plan_id, version=expected_version)
        if not placement_diff(previous, candidate)["updated"]:
            return _modify_result("unchanged", validation, updated_device=updated,
                           plan_id=plan_id, version=expected_version)

        saved = draft_save_placements(
            project_id, candidate, validation, plan_id=plan_id, expected_version=expected_version,
        )
        return _modify_result(saved["status"], validation, updated_device=updated,
                       plan_id=plan_id, version=saved["version"])
    except _PlacementValidationUnavailable as error:
        return _modify_failure("validation_unavailable", error.code, error)
    except PlacementVersionConflict as error:
        return _modify_failure("conflict", "version_conflict", error)
    except (ValueError, TypeError, OverflowError, ImportError, OSError, RuntimeError, ProjectDatabaseError) as error:
        return _modify_failure("failed", "edit_failed", error)


def ifc_remove_device(
    device_id: str,
    reason: str,
    *,
    project_id: str,
    plan_id: str,
    expected_version: int,
    validate_placements=None,
    placement_context=None,
    rules=None,
    target: str = "draft",
) -> dict:
    """Remove a proposal by ID or stage an IFC removal by managed GlobalId.

    Return the staged removal ID, affected references and draft revision.
    The approved batch workflow removes the device from the IFC.
    """
    try:
        validate_plan_key(project_id, plan_id, expected_version)
        if not isinstance(device_id, str) or not device_id.strip() or device_id != device_id.strip():
            raise ValueError("device_id must be a non-empty identifier")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        if target not in ("draft", "ifc"):
            raise ValueError("target must be 'draft' or 'ifc'")
        store = ProjectStore()
        draft = store.get_placement_plan(project_id, plan_id)
        if draft is None:
            raise PlacementPlanNotFound("Placement plan was not found in this project")
        if draft["version"] != expected_version:
            raise PlacementVersionConflict(
                f"Expected version {expected_version}, current version is {draft['version']}"
            )

        proposed_id, references = _find_removal_target(project_id, plan_id, device_id, target)
        proposals = draft["proposals"]
        remaining = [item for item in proposals if item["proposed_id"] != proposed_id]
        if len(remaining) == len(proposals):
            raise ValueError("Device was not found in the latest draft for this plan")
        if any(reference["blocks_removal"] for reference in references):
            return _remove_result(
                "blocked", references=references, plan_id=plan_id, version=expected_version,
                errors=["Resolve the external IFC references before removing this device"],
            )

        validation = _run_placement_validator(
            remaining, validate_placements=validate_placements, placement_context=placement_context, rules=rules,
        )
        if not validation["valid"]:
            return _remove_result("validation_failed", references=references, plan_id=plan_id,
                           version=expected_version, validation=validation)

        saved = store.save_placement_plan(
            project_id, remaining, validation, plan_id=plan_id, expected_version=expected_version,
            removal={
                "proposed_id": proposed_id, "device_id": device_id, "reason": reason.strip(),
                "target": target, "affected_references": references,
            },
        )
        return _remove_result(saved["status"], removed_id=device_id, references=references,
                       plan_id=plan_id, version=saved["version"], validation=validation)
    except _PlacementValidationUnavailable as error:
        return _remove_result("validation_unavailable", errors=[str(error)])
    except PlacementVersionConflict as error:
        return _remove_result("conflict", errors=[str(error)])
    except (PlacementPlanNotFound, ValueError, TypeError, ImportError, OSError, RuntimeError, ProjectDatabaseError) as error:
        return _remove_result("failed", errors=[str(error)])


# Shared placement validation

class _PlacementValidationUnavailable(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _require_identifier(value, field):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be a non-empty string without surrounding whitespace")


def _validate_position(position, field):
    if isinstance(position, dict) and {"x", "y", "z"} <= position.keys():
        coordinates = [position[axis] for axis in ("x", "y", "z")]
    elif isinstance(position, (list, tuple)) and len(position) == 3:
        coordinates = position
    else:
        raise ValueError(f"{field} must contain x, y and z coordinates")

    for coordinate in coordinates:
        if type(coordinate) is int:
            continue
        if type(coordinate) is not float or not math.isfinite(coordinate):
            raise ValueError(f"{field} must contain finite numeric coordinates")


def _validate_proposals(proposals):
    if not isinstance(proposals, list):
        raise ValueError("proposals must be a list")
    seen = set()
    for index, proposal in enumerate(proposals):
        field = f"proposals[{index}]"
        if not isinstance(proposal, dict):
            raise ValueError(f"{field} must be an object")
        proposed_id = proposal.get("proposed_id")
        _require_identifier(proposed_id, f"{field}.proposed_id")
        if proposed_id in seen:
            raise ValueError(f"Duplicate proposed_id: {proposed_id}")
        seen.add(proposed_id)
        for key in ("device_type", "space_id"):
            _require_identifier(proposal.get(key), f"{field}.{key}")
        _validate_position(proposal.get("position"), f"{field}.position")
        if "host_id" in proposal and proposal["host_id"] is not None:
            _require_identifier(proposal["host_id"], f"{field}.host_id")
        if "rule_ids" in proposal:
            if not isinstance(proposal["rule_ids"], list):
                raise ValueError(f"{field}.rule_ids must be a list")
            for rule_id in proposal["rule_ids"]:
                _require_identifier(rule_id, f"{field}.rule_ids item")


def _validate_placement_result(result):
    if not isinstance(result, dict) or type(result.get("valid")) is not bool:
        raise ValueError("validation_result must be an object with a boolean valid field")
    for field in ("errors", "warnings", "suggested_fixes"):
        if not isinstance(result.get(field), list):
            raise ValueError(f"validation_result.{field} must be a list")
    if result["valid"] and result["errors"]:
        raise ValueError("validation_result cannot be valid while reporting errors")


def _run_placement_validator(proposals, *, validate_placements, placement_context, rules):
    """Validate the candidate with the backend's engine without exposing it to mutation."""
    if not callable(validate_placements) or placement_context is None or rules is None:
        raise _PlacementValidationUnavailable(
            "validator_required", "Provide engine_validate_placements, placement_context and rules",
        )
    try:
        result = validate_placements(
            proposals=deepcopy(proposals), placement_context=placement_context, rules=rules,
        )
        _validate_placement_result(result)
        return json.loads(json.dumps(result, allow_nan=False, sort_keys=True))
    except Exception as error:
        raise _PlacementValidationUnavailable("validator_failed", str(error)) from error


# Approved batch application

def _batch_result(status, model_version=None, *, created=(), updated=(), removed=(), errors=()):
    return {
        "status": status,
        "created_ids": list(created),
        "updated_ids": list(updated),
        "removed_ids": list(removed),
        "errors": list(errors),
        "model_version": model_version,
    }


def _apply_approved_batch(connection, snapshot_dir, project_id, plan_id, version):
    proposals, validation = load_plan(connection, project_id, plan_id, version)
    approved_version = latest_approved_version(connection, project_id, plan_id)
    if approved_version is None or validation.get("valid") is not True or validation.get("errors"):
        raise PlacementApplicationError("This placement version has not passed validation and approval")

    receipt = latest_applied_version(connection, project_id, plan_id)
    model_row = get_model_row(connection, project_id)
    if model_row is None:
        raise PlacementApplicationError("Register the project's IFC baseline before applying a plan")
    source = verified_model_path(snapshot_dir, model_row)
    model_version = model_row["version"]

    if receipt is not None and version <= receipt:
        if has_application(connection, project_id, plan_id, version):
            return _batch_result("already_applied", model_version)
        raise PlacementApplicationError("A newer version of this placement plan has already been applied")
    if approved_version != version:
        raise PlacementApplicationError("Apply the latest approved version of this placement plan")

    from ifc_engine.writer import device_spec, open_model, verify_batch, write_batch

    previous = [] if receipt is None else load_plan(connection, project_id, plan_id, receipt)[0]
    diff = placement_diff(previous, proposals)
    before = {item["proposed_id"]: device_spec(project_id, plan_id, item) for item in previous}
    current = {item["proposed_id"]: device_spec(project_id, plan_id, item) for item in proposals}
    removed = [before[item_id] for item_id in diff["removed"]]
    model = open_model(source)
    if diff["added"] or diff["updated"] or diff["removed"]:
        write_batch(model, before, current, diff)
        model_version += 1
        publish_model(
            connection, snapshot_dir, project_id, model_version,
            lambda path: model.write(str(path)),
            lambda path: verify_batch(open_model(path), current, removed),
        )
    else:
        verify_batch(model, current, removed)
    record_application(connection, project_id, plan_id, version, model_version)
    return _batch_result(
        "applied", model_version,
        created=(current[item_id].guid for item_id in diff["added"]),
        updated=(current[item_id].guid for item_id in diff["updated"]),
        removed=(spec.guid for spec in removed),
    )


# Device edits

def _modify_result(status, validation_result, *, updated_device=None, plan_id=None, version=None):
    return {
        "status": status,
        "updated_device": updated_device,
        "validation_result": validation_result,
        "plan_id": plan_id,
        "version": version,
    }


def _modify_failure(status, code, message):
    return _modify_result(status, {
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


# Device removals

def _remove_result(status, *, removed_id=None, references=(), errors=(), plan_id=None, version=None, validation=None):
    return {
        "status": status,
        "removed_id": removed_id,
        "affected_references": list(references),
        "errors": list(errors),
        "plan_id": plan_id,
        "version": version,
        "validation_result": validation,
    }


def _find_removal_target(project_id, plan_id, device_id, target):
    """Resolve the proposal ID and inspect IFC references if the device is already placed."""
    applied, path = get_applied_placement_plan(project_id, plan_id, required=(target == "ifc"))
    if not applied:
        if target == "ifc":
            raise ValueError("IFC GlobalId is not a managed device in this project's applied plan")
        return device_id, []

    from ifc_engine.writer import device_guid, device_spec, get_removal_references, open_model

    for proposal in applied:
        proposed_id = proposal["proposed_id"]
        lookup_id = device_guid(project_id, plan_id, proposed_id) if target == "ifc" else proposed_id
        if device_id == lookup_id:
            spec = device_spec(project_id, plan_id, proposal)
            return proposed_id, get_removal_references(open_model(path), spec)
    if target == "ifc":
        raise ValueError("IFC GlobalId is not a managed device in this project's applied plan")
    return device_id, []
