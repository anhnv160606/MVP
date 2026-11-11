"""Apply an approved placement plan from the trusted Orchestrator."""

import sqlite3

from database.placement_application import (
    PlacementApplicationError,
    application_transaction,
    get_model_row,
    load_plan,
    publish_model,
    validate_plan_key,
    verified_model_path,
)
from database.project_store import placement_diff


def _result(status, model_version=None, *, created=(), updated=(), removed=(), errors=()):
    return {
        "status": status,
        "created_ids": list(created),
        "updated_ids": list(updated),
        "removed_ids": list(removed),
        "errors": list(errors),
        "model_version": model_version,
    }


def _apply(connection, snapshot_dir, project_id, plan_id, version):
    proposals, validation = load_plan(connection, project_id, plan_id, version)
    approved_version = connection.execute(
        "SELECT MAX(version) FROM placement_approvals WHERE project_id = ? AND plan_id = ?",
        (project_id, plan_id),
    ).fetchone()[0]
    if approved_version is None or validation.get("valid") is not True or validation.get("errors"):
        raise PlacementApplicationError("This placement version has not passed validation and approval")

    receipt = connection.execute(
        """SELECT MAX(plan_version) FROM placement_applications
           WHERE project_id = ? AND plan_id = ?""",
        (project_id, plan_id),
    ).fetchone()[0]
    model_row = get_model_row(connection, project_id)
    if model_row is None:
        raise PlacementApplicationError("Register the project's IFC baseline before applying a plan")
    source = verified_model_path(snapshot_dir, model_row)
    model_version = model_row["version"]

    if receipt is not None and version <= receipt:
        already_applied = connection.execute(
            """SELECT 1 FROM placement_applications
               WHERE project_id = ? AND plan_id = ? AND plan_version = ?""",
            (project_id, plan_id, version),
        ).fetchone()
        if already_applied is not None:
            return _result("already_applied", model_version)
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
    connection.execute(
        "INSERT INTO placement_applications VALUES (?, ?, ?, ?)",
        (project_id, plan_id, version, model_version),
    )
    return _result(
        "applied", model_version,
        created=(current[item_id].guid for item_id in diff["added"]),
        updated=(current[item_id].guid for item_id in diff["updated"]),
        removed=(spec.guid for spec in removed),
    )


def ifc_apply_placements_batch(approved_plan_id: str, version: int, project_id: str) -> dict:
    """Apply a recorded approval. Keep this backend function out of the agent tool registry."""
    try:
        validate_plan_key(project_id, approved_plan_id, version)
        with application_transaction() as (connection, snapshot_dir):
            return _apply(connection, snapshot_dir, project_id, approved_plan_id, version)
    except (PlacementApplicationError, ImportError, OSError, RuntimeError, sqlite3.Error) as error:
        return _result("failed", errors=[str(error)])
