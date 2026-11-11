"""Approval and version metadata for Phase 2 IFC snapshots."""

import hashlib
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from database.postgres import project_transaction


class PlacementApplicationError(ValueError):
    """An approval or IFC prerequisite is missing or inconsistent."""


def validate_plan_key(project_id, plan_id, version):
    for name, value in (("project_id", project_id), ("plan_id", plan_id)):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise PlacementApplicationError(f"{name} must be a non-empty identifier")
    if type(version) is not int or not 1 <= version <= 2**63 - 1:
        raise PlacementApplicationError("version must be a positive 64-bit integer")


def _snapshot_dir():
    location = os.environ.get("MVP_IFC_SNAPSHOT_DIR")
    if not location:
        raise PlacementApplicationError("Set MVP_IFC_SNAPSHOT_DIR to a persistent IFC directory")
    return Path(location).resolve()


@contextmanager
def application_transaction(project_id):
    """Serialize IFC publication with other writes for this project."""
    snapshot_dir = _snapshot_dir()
    with project_transaction(lock_project=project_id) as connection:
        yield connection, snapshot_dir


def load_plan(connection, project_id, plan_id, version):
    row = connection.execute(
        """SELECT proposals, validation_result FROM placement_plan_versions
           WHERE project_id = %s AND plan_id = %s AND version = %s""",
        (project_id, plan_id, version),
    ).fetchone()
    if row is None:
        raise PlacementApplicationError("Placement plan version was not found in this project")
    return row["proposals"], row["validation_result"]


def latest_approved_version(connection, project_id, plan_id):
    return connection.execute(
        "SELECT MAX(version) AS version FROM placement_approvals WHERE project_id = %s AND plan_id = %s",
        (project_id, plan_id),
    ).fetchone()["version"]


def latest_applied_version(connection, project_id, plan_id):
    return connection.execute(
        """SELECT MAX(plan_version) AS version FROM placement_applications
           WHERE project_id = %s AND plan_id = %s""",
        (project_id, plan_id),
    ).fetchone()["version"]


def has_application(connection, project_id, plan_id, version):
    return connection.execute(
        """SELECT 1 FROM placement_applications
           WHERE project_id = %s AND plan_id = %s AND plan_version = %s""",
        (project_id, plan_id, version),
    ).fetchone() is not None


def record_application(connection, project_id, plan_id, version, model_version):
    connection.execute(
        """INSERT INTO placement_applications (project_id, plan_id, plan_version, model_version)
           VALUES (%s, %s, %s, %s)""",
        (project_id, plan_id, version, model_version),
    )


def approve_placement_plan(project_id, plan_id, version, *, reviewer_id):
    """Record a trusted review decision. Caller authenticates the engineer."""
    validate_plan_key(project_id, plan_id, version)
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise PlacementApplicationError("An authenticated reviewer ID is required")
    with project_transaction(lock_project=project_id) as connection:
        _, validation = load_plan(connection, project_id, plan_id, version)
        if validation.get("valid") is not True or validation.get("errors"):
            raise PlacementApplicationError("Only a placement plan that passed validation can be approved")
        latest = connection.execute(
            """SELECT MAX(version) AS version FROM placement_plan_versions
               WHERE project_id = %s AND plan_id = %s""",
            (project_id, plan_id),
        ).fetchone()["version"]
        if latest != version:
            raise PlacementApplicationError("A newer draft exists. Review the latest version.")
        connection.execute(
            """INSERT INTO placement_approvals (project_id, plan_id, version, reviewer_id)
               VALUES (%s, %s, %s, %s) ON CONFLICT (project_id, plan_id, version) DO NOTHING""",
            (project_id, plan_id, version, reviewer_id),
        )


def get_model_row(connection, project_id, version=None):
    if version is None:
        return connection.execute(
            """SELECT * FROM project_ifc_versions WHERE project_id = %s
               ORDER BY version DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
    return connection.execute(
        "SELECT * FROM project_ifc_versions WHERE project_id = %s AND version = %s",
        (project_id, version),
    ).fetchone()


def get_applied_placement_plan(project_id, plan_id, *, required=True):
    """Read the applied proposals and current model path from the same DB snapshot."""
    with project_transaction(lock_project=project_id) as connection:
        applied_version = latest_applied_version(connection, project_id, plan_id)
        if applied_version is None:
            if not required:
                return [], None
            raise PlacementApplicationError("This plan has not been applied to IFC")
        proposals, _ = load_plan(connection, project_id, plan_id, applied_version)
        row = get_model_row(connection, project_id)
        if row is None:
            raise PlacementApplicationError("The project's IFC model was not found")
    return proposals, verified_model_path(_snapshot_dir(), row)


def _sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def verified_model_path(snapshot_dir, row):
    path = snapshot_dir / row["filename"]
    if _sha256(path) != row["sha256"]:
        raise PlacementApplicationError(f"IFC snapshot {row['version']} failed its integrity check")
    return path


def publish_model(connection, snapshot_dir, project_id, version, write, verify):
    """Verify a private file before publishing its immutable path in PostgreSQL."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=snapshot_dir, suffix=".ifc", delete=False) as file:
        temporary = Path(file.name)
    try:
        write(temporary)
        verify(temporary)
        with temporary.open("r+b") as file:
            os.fsync(file.fileno())
        digest = _sha256(temporary)
        filename = f"{uuid4().hex}.ifc"
        # Same directory: the file is fully present before PostgreSQL can reference it.
        os.replace(temporary, snapshot_dir / filename)
        connection.execute(
            "INSERT INTO project_ifc_versions VALUES (%s, %s, %s, %s)",
            (project_id, version, filename, digest),
        )
    finally:
        temporary.unlink(missing_ok=True)


def register_project_ifc(project_id, source_path):
    """Copy the architectural IFC once as version 0, through the trusted backend."""
    from ifc_engine.writer import open_model

    validate_plan_key(project_id, "baseline", 1)
    source = Path(source_path)
    with application_transaction(project_id) as (connection, snapshot_dir):
        if get_model_row(connection, project_id) is not None:
            raise PlacementApplicationError("This project already has an IFC baseline")
        publish_model(
            connection, snapshot_dir, project_id, 0,
            lambda path: shutil.copyfile(source, path), open_model,
        )


def get_project_ifc_path(project_id, version=None):
    """Return the verified IFC snapshot path for viewing or export."""
    with project_transaction() as connection:
        row = get_model_row(connection, project_id, version)
    if row is None:
        raise PlacementApplicationError("Project IFC version was not found")
    return verified_model_path(_snapshot_dir(), row)
