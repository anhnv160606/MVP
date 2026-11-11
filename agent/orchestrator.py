"""Internal Orchestrator entry points for approved writes."""

from tools.ifc_write import ifc_apply_placements_batch


def apply_approved_placement_plan(approved_plan_id: str, version: int, project_id: str) -> dict:
    """Called after review through the trusted backend, outside the LLM tool loop."""
    return ifc_apply_placements_batch(approved_plan_id, version, project_id)
