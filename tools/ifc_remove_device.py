"""Stage the removal of a Phase 2 electrical device for review."""

import sqlite3

from database.placement_application import get_applied_placement_plan, validate_plan_key
from database.project_store import PlacementPlanNotFound, PlacementVersionConflict, ProjectStore
from tools.draft_save_placements import PlacementValidationUnavailable, run_placement_validator


def _result(status, *, removed_id=None, references=(), errors=(), plan_id=None, version=None, validation=None):
    return {
        "status": status,
        "removed_id": removed_id,
        "affected_references": list(references),
        "errors": list(errors),
        "plan_id": plan_id,
        "version": version,
        "validation_result": validation,
    }


def _find_target(project_id, plan_id, device_id, target):
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

    The trusted backend provides the project scope and validator. The IFC is
    changed by the existing approved batch workflow.
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

        proposed_id, references = _find_target(project_id, plan_id, device_id, target)
        proposals = draft["proposals"]
        remaining = [item for item in proposals if item["proposed_id"] != proposed_id]
        if len(remaining) == len(proposals):
            raise ValueError("Device was not found in the latest draft for this plan")
        if any(reference["blocks_removal"] for reference in references):
            return _result(
                "blocked", references=references, plan_id=plan_id, version=expected_version,
                errors=["Resolve the external IFC references before removing this device"],
            )

        validation = run_placement_validator(
            remaining, validate_placements=validate_placements, placement_context=placement_context, rules=rules,
        )
        if not validation["valid"]:
            return _result("validation_failed", references=references, plan_id=plan_id,
                           version=expected_version, validation=validation)

        saved = store.save_placement_plan(
            project_id, remaining, validation, plan_id=plan_id, expected_version=expected_version,
            removal={
                "proposed_id": proposed_id, "device_id": device_id, "reason": reason.strip(),
                "target": target, "affected_references": references,
            },
        )
        return _result(saved["status"], removed_id=device_id, references=references,
                       plan_id=plan_id, version=saved["version"], validation=validation)
    except PlacementValidationUnavailable as error:
        return _result("validation_unavailable", errors=[str(error)])
    except PlacementVersionConflict as error:
        return _result("conflict", errors=[str(error)])
    except (PlacementPlanNotFound, ValueError, TypeError, ImportError, OSError, RuntimeError, sqlite3.Error) as error:
        return _result("failed", errors=[str(error)])
