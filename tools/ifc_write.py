"""Phase 2 placement tools and their backend-only persistence functions.

The tools use the mvp_placement PostgreSQL schema and immutable IFC snapshots.
Configure MVP_DATABASE_URL and MVP_IFC_SNAPSHOT_DIR in the backend. All callers
must share the snapshot directory. Only trusted review code records approvals,
and only the Orchestrator calls ifc_apply_placements_batch.
"""

import hashlib
import json
import os
import re
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from shutil import copyfile
from tempfile import NamedTemporaryFile
from uuid import uuid4

from ifc_engine import writer


_TYPE_FIELDS = {
    "ifc_class", "module_id", "geometry_ref", "dimensions_m", "specs", "rated_power", "rated_power_W",
    "load_class_id", "terminal_class_id", "connection_specs", "voltage", "phases",
}
_EDITABLE_FIELDS = _TYPE_FIELDS | {
    "device_type", "space_id", "host_id", "position", "orientation", "rule_ids", "properties", "reason",
    "requirement_id", "requirement_unit",
}
_MAX_VERSION = 2**63 - 1


class PlacementError(ValueError):
    """A placement request or its persisted state is invalid."""


class PlacementConflict(PlacementError):
    """The plan or model changed during an edit."""


class PlacementDatabaseError(RuntimeError):
    """The Project DB operation failed."""


class _ValidationUnavailable(PlacementError):
    """The placement engine could not validate the proposal snapshot."""


@contextmanager
def _project_transaction(project_id=None):
    """Lock all placement writes for a project, including approval and publication."""
    try:
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
    except ImportError as error:
        raise PlacementDatabaseError("Install psycopg with binary support for the Phase 2 Project DB") from error

    dsn = os.environ.get("MVP_DATABASE_URL")
    if not dsn:
        raise PlacementDatabaseError("Set MVP_DATABASE_URL to the backend PostgreSQL connection string")
    schema = os.environ.get("MVP_POSTGRES_SCHEMA", "mvp_placement")
    try:
        with psycopg.connect(dsn, row_factory=dict_row, prepare_threshold=None, connect_timeout=10) as db:
            db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            db.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog, pg_temp").format(sql.Identifier(schema)))
            if project_id is not None:
                db.execute("SET LOCAL lock_timeout TO '30s'")
                db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{schema}:{project_id}",))
            yield db
    except psycopg.Error as error:
        if error.sqlstate in {"42P01", "3F000"}:
            raise PlacementDatabaseError(f"The placement schema {schema!r} must be provisioned in Project DB") from error
        detail = error.diag.message_primary
        raise PlacementDatabaseError(detail or "Project DB connection failed") from error


def _identifier(value, field):
    if not isinstance(value, str) or not value.strip() or value != value.strip() or "\0" in value:
        raise PlacementError(f"{field} must be a non-empty identifier without surrounding whitespace")


def _plan_key(project_id, plan_id, version=None):
    _identifier(project_id, "project_id")
    _identifier(plan_id, "plan_id")
    if version is not None and (type(version) is not int or not 1 <= version <= _MAX_VERSION):
        raise PlacementError("version must be a positive 64-bit integer")


def _require_revision(project_id, plan_id, version):
    _plan_key(project_id, plan_id, version)
    if version is None:
        raise PlacementError("A version is required for this operation")


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError, OverflowError) as error:
        raise PlacementError("Placement data must be JSON-compatible") from error


def _validate_proposals(proposals):
    if not isinstance(proposals, list):
        raise PlacementError("proposals must be a list")
    seen = set()
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            raise PlacementError(f"proposals[{index}] must be an object")
        for field in ("proposed_id", "device_type", "space_id"):
            _identifier(proposal.get(field), f"proposals[{index}].{field}")
        proposed_id = proposal["proposed_id"]
        if proposed_id in seen:
            raise PlacementError(f"Duplicate proposed_id: {proposed_id}")
        seen.add(proposed_id)
        writer.coordinates(proposal.get("position"), f"proposals[{index}].position")
        if proposal.get("host_id") is not None:
            _identifier(proposal["host_id"], f"proposals[{index}].host_id")
        rule_ids = proposal.get("rule_ids", [])
        if not isinstance(rule_ids, list):
            raise PlacementError("rule_ids must be a list")
        for rule_id in rule_ids:
            _identifier(rule_id, "rule_id")
        properties = proposal.get("properties", {})
        if not isinstance(properties, dict):
            raise PlacementError("properties must be an object")
        for key in properties:
            _identifier(key, "property name")


def _validate_result(result):
    if not isinstance(result, dict) or type(result.get("valid")) is not bool:
        raise PlacementError("validation_result must have a boolean valid field")
    for field in ("errors", "warnings", "suggested_fixes"):
        if not isinstance(result.get(field), list):
            raise PlacementError(f"validation_result.{field} must be a list")
    if result["valid"] and result["errors"]:
        raise PlacementError("A valid result cannot contain errors")


def _placement_diff(previous, current):
    before = {item["proposed_id"]: _json(item) for item in previous}
    after = {item["proposed_id"]: _json(item) for item in current}
    shared = before.keys() & after.keys()
    updated = {key for key in shared if before[key] != after[key]}
    return {
        "added": sorted(after.keys() - before.keys()),
        "updated": sorted(updated),
        "removed": sorted(before.keys() - after.keys()),
        "unchanged": sorted(shared - updated),
    }


def _plan_row(db, project_id, plan_id, version=None):
    query = "SELECT * FROM placement_plan_versions WHERE project_id = %s AND plan_id = %s"
    if version is None:
        return db.execute(query + " ORDER BY version DESC LIMIT 1", (project_id, plan_id)).fetchone()
    return db.execute(query + " AND version = %s", (project_id, plan_id, version)).fetchone()


def _require_plan(db, project_id, plan_id, version=None):
    row = _plan_row(db, project_id, plan_id, version)
    if row is None:
        raise PlacementError("Placement plan version not found in this project")
    return row


def _check_version(row, expected):
    if row["version"] != expected:
        raise PlacementConflict(f"Expected draft version {expected}, current version is {row['version']}")


def _save_revision(db, project_id, plan_id, version, previous, proposals, validation, *, removal=None):
    _require_revision(project_id, plan_id, version)
    diff = _placement_diff(previous, proposals)
    db.execute(
        """INSERT INTO placement_plan_versions
           (project_id, plan_id, version, status, proposals, validation_result, diff_summary)
           VALUES (%s, %s, %s, 'pending_review', %s::jsonb, %s::jsonb, %s::jsonb)""",
        (project_id, plan_id, version, _json(proposals), _json(validation), _json(diff)),
    )
    if removal is not None:
        db.execute(
            """INSERT INTO placement_removals
               (project_id, plan_id, version, proposed_id, device_id, target, reason, affected_references)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)""",
            (project_id, plan_id, version, removal["proposed_id"], removal["device_id"],
             removal["target"], removal["reason"], _json(removal["references"])),
        )
    return {"plan_id": plan_id, "version": version, "status": "pending_review", "diff_summary": diff}


def draft_save_placements(project_id, proposals, validation_result, *, plan_id=None, expected_version=None):
    """Save the full proposal snapshot, including failed validation, for review.

    Omit plan_id for a new plan. To append a revision, pass plan_id and the
    current expected_version. The caller supplies that revision's tool #9 result.
    """
    _identifier(project_id, "project_id")
    if plan_id is not None:
        _require_revision(project_id, plan_id, expected_version)
    elif expected_version is not None:
        raise PlacementError("expected_version requires plan_id")

    _validate_proposals(proposals)
    _validate_result(validation_result)
    proposals = json.loads(_json(proposals))
    validation_result = json.loads(_json(validation_result))
    with _project_transaction(project_id) as db:
        if plan_id is None:
            return _save_revision(db, project_id, str(uuid4()), 1, [], proposals, validation_result)
        previous = _require_plan(db, project_id, plan_id)
        _check_version(previous, expected_version)
        return _save_revision(
            db, project_id, plan_id, expected_version + 1, previous["proposals"], proposals, validation_result,
        )


def get_placement_plan(project_id, plan_id, version=None):
    """Read a persisted draft revision for the review screen."""
    _plan_key(project_id, plan_id, version)
    with _project_transaction() as db:
        row = _plan_row(db, project_id, plan_id, version)
        if row is not None:
            row["created_at"] = row["created_at"].isoformat()
        return row


def get_placement_removal(project_id, plan_id, version):
    """Read the removal audit associated with an immutable draft revision."""
    _require_revision(project_id, plan_id, version)
    with _project_transaction() as db:
        row = db.execute(
            "SELECT * FROM placement_removals WHERE project_id = %s AND plan_id = %s AND version = %s",
            (project_id, plan_id, version),
        ).fetchone()
        if row is not None:
            row["created_at"] = row["created_at"].isoformat()
        return row


def approve_placement_plan(project_id, plan_id, version, *, reviewer_id):
    """Record approval after the backend authenticates and authorizes the engineer."""
    _require_revision(project_id, plan_id, version)
    _identifier(reviewer_id, "reviewer_id")
    with _project_transaction(project_id) as db:
        row = _require_plan(db, project_id, plan_id)
        _check_version(row, version)
        validation = row["validation_result"]
        _validate_result(validation)
        if not validation["valid"]:
            raise PlacementError("Only a plan that passed validation can be approved")
        db.execute(
            """INSERT INTO placement_approvals (project_id, plan_id, version, reviewer_id)
               VALUES (%s, %s, %s, %s) ON CONFLICT (project_id, plan_id, version) DO NOTHING""",
            (project_id, plan_id, version, reviewer_id),
        )


def _model_row(db, project_id, version=None):
    query = "SELECT * FROM project_ifc_versions WHERE project_id = %s"
    if version is None:
        return db.execute(query + " ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
    return db.execute(query + " AND version = %s", (project_id, version)).fetchone()


def _snapshot_dir():
    location = os.environ.get("MVP_IFC_SNAPSHOT_DIR")
    if not location:
        raise PlacementError("Set MVP_IFC_SNAPSHOT_DIR to a shared, persistent IFC directory")
    return Path(location).resolve()


def _digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _model_path(row):
    if row is None:
        raise PlacementError("Project IFC baseline or requested model version not found")
    filename = row["filename"]
    if re.fullmatch(r"[a-f0-9]{32}\.ifc", filename) is None:
        raise PlacementError("Invalid IFC snapshot filename in Project DB")
    path = _snapshot_dir() / filename
    if _digest(path) != row["sha256"]:
        raise PlacementError(f"IFC snapshot {row['version']} failed its integrity check")
    return path


@contextmanager
def _publish_snapshot(db, project_id, version, write, verify):
    folder = _snapshot_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=folder, suffix=".ifc", delete=False) as file:
        temporary = Path(file.name)
    try:
        write(temporary)
        verify(temporary)
        with temporary.open("r+b") as file:
            os.fsync(file.fileno())
        filename = f"{uuid4().hex}.ifc"
        db.execute(
            "INSERT INTO project_ifc_versions (project_id, version, filename, sha256) VALUES (%s, %s, %s, %s)",
            (project_id, version, filename, _digest(temporary)),
        )
        yield
        # Publish only after SQL statements succeed, before their transaction commits.
        # A failure with uncertain commit status can leave an unreferenced file.
        os.replace(temporary, folder / filename)
    finally:
        temporary.unlink(missing_ok=True)


def register_project_ifc(project_id, source_path):
    """Register the uploaded architectural IFC as model version 0."""
    _identifier(project_id, "project_id")
    with _project_transaction(project_id) as db:
        if _model_row(db, project_id) is not None:
            raise PlacementError("This project already has an IFC baseline")
        with _publish_snapshot(db, project_id, 0, lambda path: copyfile(source_path, path), writer.open_model):
            pass


def get_project_ifc_path(project_id, version=None):
    """Return the verified IFC snapshot for viewing or export."""
    _identifier(project_id, "project_id")
    if version is not None and (type(version) is not int or not 0 <= version <= _MAX_VERSION):
        raise PlacementError("model version must be a non-negative 64-bit integer")
    with _project_transaction() as db:
        return _model_path(_model_row(db, project_id, version))


def _application_row(db, project_id, plan_id, version=None):
    query = "SELECT * FROM placement_applications WHERE project_id = %s AND plan_id = %s"
    if version is None:
        return db.execute(query + " ORDER BY plan_version DESC LIMIT 1", (project_id, plan_id)).fetchone()
    return db.execute(query + " AND plan_version = %s", (project_id, plan_id, version)).fetchone()


def _record_application(db, project_id, plan_id, version, model_version):
    db.execute(
        """INSERT INTO placement_applications (project_id, plan_id, plan_version, model_version)
           VALUES (%s, %s, %s, %s)""",
        (project_id, plan_id, version, model_version),
    )


def _batch_result(status, model_version=None, *, created=(), updated=(), removed=(), errors=()):
    return {
        "status": status, "created_ids": list(created), "updated_ids": list(updated),
        "removed_ids": list(removed), "errors": list(errors), "model_version": model_version,
    }


def ifc_apply_placements_batch(approved_plan_id, version, project_id):
    """Apply the exact approved revision. Call from the Orchestrator only."""
    try:
        _require_revision(project_id, approved_plan_id, version)
        with _project_transaction(project_id) as db:
            return _apply_approved_revision(db, project_id, approved_plan_id, version)
    except (ValueError, TypeError, OSError, RuntimeError, OverflowError) as error:
        return _batch_result("failed", errors=[str(error)])


def _apply_approved_revision(db, project_id, plan_id, version):
    row = _require_plan(db, project_id, plan_id, version)
    model_row = _model_row(db, project_id)
    if _application_row(db, project_id, plan_id, version) is not None:
        _model_path(model_row)
        return _batch_result("already_applied", model_row["version"])

    latest = _require_plan(db, project_id, plan_id)
    _check_version(latest, version)
    approval = db.execute(
        "SELECT 1 FROM placement_approvals WHERE project_id = %s AND plan_id = %s AND version = %s",
        (project_id, plan_id, version),
    ).fetchone()
    validation = row["validation_result"]
    _validate_result(validation)
    if approval is None or not validation["valid"]:
        raise PlacementError("This placement revision has not passed validation and approval")

    model = writer.open_model(_model_path(model_row))
    model_version = model_row["version"]
    applied = _application_row(db, project_id, plan_id)
    previous = [] if applied is None else _require_plan(db, project_id, plan_id, applied["plan_version"])["proposals"]
    proposals = row["proposals"]
    _validate_proposals(proposals)
    diff = _placement_diff(previous, proposals)
    before = {item["proposed_id"]: writer.device_spec(project_id, plan_id, item) for item in previous}
    current = {item["proposed_id"]: writer.device_spec(project_id, plan_id, item) for item in proposals}
    removed = [before[key] for key in diff["removed"]]
    if diff["added"] or diff["updated"] or diff["removed"]:
        for key in diff["removed"] + diff["updated"]:
            writer.remove_device(model, before[key])
        for key in diff["added"] + diff["updated"]:
            writer.create_device(model, current[key])
        model_version += 1
        with _publish_snapshot(
            db, project_id, model_version,
            lambda path: model.write(str(path)),
            lambda path: writer.verify_batch(writer.open_model(path), current, removed),
        ):
            _record_application(db, project_id, plan_id, version, model_version)
    else:
        writer.verify_batch(model, current, removed)
        _record_application(db, project_id, plan_id, version, model_version)

    return _batch_result(
        "applied", model_version,
        created=(current[key].guid for key in diff["added"]),
        updated=(current[key].guid for key in diff["updated"]),
        removed=(spec.guid for spec in removed),
    )


def _run_validator(proposals, validator, placement_context, rules):
    if not callable(validator) or placement_context is None or rules is None:
        raise _ValidationUnavailable("Provide engine_validate_placements, placement_context and rules")
    try:
        result = validator(proposals=deepcopy(proposals), placement_context=placement_context, rules=rules)
        _validate_result(result)
        return json.loads(_json(result))
    except Exception as error:
        raise _ValidationUnavailable(str(error)) from error


def _merge_device(device, changes):
    updated = dict(device)
    if any(changes[key] != device.get(key) for key in ("device_type", "ifc_class", "module_id") if key in changes):
        for key in _TYPE_FIELDS | {"rule_ids"}:
            # A catalog switch within the same IFC class should keep that class.
            if key != "ifc_class" or "device_type" in changes or "ifc_class" in changes:
                updated.pop(key, None)
    if "space_id" in changes and changes["space_id"] != device["space_id"]:
        updated.pop("host_id", None)
        updated.pop("rule_ids", None)
    if "host_id" in changes and changes["host_id"] != device.get("host_id"):
        updated.pop("rule_ids", None)
    for key, value in changes.items():
        if key == "properties":
            if not isinstance(value, dict):
                raise PlacementError("properties must be an object")
            updated[key] = {**updated.get(key, {}), **value}
        elif key in _TYPE_FIELDS and value is None:
            updated.pop(key, None)
        else:
            updated[key] = value
    return updated


def _stage_device_change(
    device_id, *, project_id, plan_id, expected_version, target, changes, reason,
    validate_placements, placement_context, rules,
):
    _require_revision(project_id, plan_id, expected_version)
    _identifier(device_id, "device_id")
    if target not in ("draft", "ifc"):
        raise PlacementError("target must be 'draft' or 'ifc'")
    with _project_transaction(project_id) as db:
        draft = _require_plan(db, project_id, plan_id)
        _check_version(draft, expected_version)
        applied = _application_row(db, project_id, plan_id)
        model_row = _model_row(db, project_id)
        applied_proposals = (
            [] if applied is None else _require_plan(db, project_id, plan_id, applied["plan_version"])["proposals"]
        )

    proposed_id = device_id
    references = []
    for item in applied_proposals:
        key = item["proposed_id"]
        lookup = key if target == "draft" else writer.device_guid(project_id, plan_id, key)
        if lookup == device_id:
            proposed_id = key
            if target == "ifc" or reason is not None:
                spec = writer.device_spec(project_id, plan_id, item)
                model = writer.open_model(_model_path(model_row))
                if reason is not None:
                    references = writer.get_removal_references(model, spec)
                else:
                    writer.verify_device(model, spec)
            break
    else:
        if target == "ifc":
            raise PlacementError("IFC GlobalId is not a managed device in this project's applied plan")

    previous = draft["proposals"]
    candidate = list(previous)
    for index, proposal in enumerate(previous):
        if proposal["proposed_id"] == proposed_id:
            break
    else:
        raise PlacementError("Device was not found in the latest draft for this plan")

    if any(reference["blocks_removal"] for reference in references):
        return "blocked", None, None, references, expected_version

    removal = None
    updated = None
    if reason is None:
        updated = _merge_device(proposal, changes)
        candidate[index] = updated
        _validate_proposals(candidate)
        writer.device_spec(project_id, plan_id, updated)
    else:
        candidate.pop(index)
        removal = {
            "proposed_id": proposed_id, "device_id": device_id, "target": target,
            "reason": reason, "references": references,
        }

    validation = _run_validator(candidate, validate_placements, placement_context, rules)
    if not validation["valid"]:
        return "validation_failed", None, validation, references, expected_version

    with _project_transaction(project_id) as db:
        _check_version(_require_plan(db, project_id, plan_id), expected_version)
        if _model_row(db, project_id) != model_row:
            raise PlacementConflict("The project's IFC version changed during placement validation")
        if updated is not None and _json(updated) == _json(proposal):
            return "unchanged", updated, validation, references, expected_version
        saved = _save_revision(
            db, project_id, plan_id, expected_version + 1, previous, candidate, validation, removal=removal,
        )
    return saved["status"], updated, validation, references, saved["version"]


def ifc_modify_device(
    device_id, changes, *, project_id, plan_id, expected_version, target="draft",
    validate_placements=None, placement_context=None, rules=None,
):
    """Revalidate the entire revised plan and stage the edit for approval."""
    version = None
    updated = None
    try:
        if not isinstance(changes, dict) or not changes or changes.keys() - _EDITABLE_FIELDS:
            raise PlacementError("changes must contain supported editable fields")
        status, updated, validation, _, version = _stage_device_change(
            device_id, project_id=project_id, plan_id=plan_id, expected_version=expected_version, target=target,
            changes=json.loads(_json(changes)), reason=None, validate_placements=validate_placements,
            placement_context=placement_context, rules=rules,
        )
    except (ValueError, TypeError, OSError, RuntimeError, OverflowError) as error:
        status = _edit_failure_status(error)
        validation = {"valid": False, "errors": [{"code": status, "message": str(error)}],
                      "warnings": [], "suggested_fixes": []}
    return {"status": status, "updated_device": updated, "validation_result": validation,
            "plan_id": plan_id, "version": version}


def ifc_remove_device(
    device_id, reason, *, project_id, plan_id, expected_version, target="draft",
    validate_placements=None, placement_context=None, rules=None,
):
    """Check IFC references and stage a removal with its audit reason."""
    removed_id = None
    references = []
    validation = None
    version = None
    errors = []
    try:
        if not isinstance(reason, str) or not reason.strip() or "\0" in reason:
            raise PlacementError("reason must be a non-empty string")
        status, _, validation, references, version = _stage_device_change(
            device_id, project_id=project_id, plan_id=plan_id, expected_version=expected_version, target=target,
            changes=None, reason=reason.strip(), validate_placements=validate_placements,
            placement_context=placement_context, rules=rules,
        )
        if status == "pending_review":
            removed_id = device_id
        if status == "blocked":
            errors = ["Resolve the external IFC references before removing this device"]
    except (ValueError, TypeError, OSError, RuntimeError, OverflowError) as error:
        status = _edit_failure_status(error)
        errors = [str(error)]
    return {"status": status, "removed_id": removed_id, "affected_references": references, "errors": errors,
            "plan_id": plan_id, "version": version, "validation_result": validation}


def _edit_failure_status(error):
    if isinstance(error, _ValidationUnavailable):
        return "validation_unavailable"
    if isinstance(error, PlacementConflict):
        return "conflict"
    return "failed"
