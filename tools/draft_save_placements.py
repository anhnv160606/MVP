"""Phase 2 tool for saving electrical placement drafts for review."""

import math

from database.project_store import ProjectStore


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


def _validate_result(result):
    if not isinstance(result, dict) or type(result.get("valid")) is not bool:
        raise ValueError("validation_result must be an object with a boolean valid field")
    for field in ("errors", "warnings", "suggested_fixes"):
        if not isinstance(result.get(field), list):
            raise ValueError(f"validation_result.{field} must be a list")
    if result["valid"] and result["errors"]:
        raise ValueError("validation_result cannot be valid while reporting errors")


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
    _validate_result(validation_result)

    return ProjectStore().save_placement_plan(
        project_id, proposals, validation_result,
        plan_id=plan_id, expected_version=expected_version,
    )
