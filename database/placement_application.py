"""Approval and version metadata for Phase 2 IFC snapshots."""

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from database.project_store import ProjectStore


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS placement_approvals (
        project_id TEXT NOT NULL, plan_id TEXT NOT NULL, version INTEGER NOT NULL,
        reviewer_id TEXT NOT NULL, approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project_id, plan_id, version)
    )""",
    """CREATE TABLE IF NOT EXISTS project_ifc_versions (
        project_id TEXT NOT NULL, version INTEGER NOT NULL, filename TEXT NOT NULL,
        sha256 TEXT NOT NULL, PRIMARY KEY (project_id, version)
    )""",
    """CREATE TABLE IF NOT EXISTS placement_applications (
        project_id TEXT NOT NULL, plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL,
        model_version INTEGER NOT NULL,
        PRIMARY KEY (project_id, plan_id, plan_version)
    )""",
)


class PlacementApplicationError(ValueError):
    """An approval or IFC prerequisite is missing or inconsistent."""


def validate_plan_key(project_id, plan_id, version):
    for name, value in (("project_id", project_id), ("plan_id", plan_id)):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise PlacementApplicationError(f"{name} must be a non-empty identifier")
    if type(version) is not int or not 1 <= version <= 2**63 - 1:
        raise PlacementApplicationError("version must be a positive SQLite integer")


@contextmanager
def application_transaction():
    path = ProjectStore().db_path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_dir = path.with_name(path.name + ".ifc")
    with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        for statement in _SCHEMA:
            connection.execute(statement)
        yield connection, snapshot_dir


def load_plan(connection, project_id, plan_id, version):
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'placement_plan_versions'"
    ).fetchone()
    if table is not None:
        row = connection.execute(
            """SELECT proposals, validation_result FROM placement_plan_versions
               WHERE project_id = ? AND plan_id = ? AND version = ?""",
            (project_id, plan_id, version),
        ).fetchone()
        if row is not None:
            return json.loads(row["proposals"]), json.loads(row["validation_result"])
    raise PlacementApplicationError("Placement plan version was not found in this project")


def approve_placement_plan(project_id, plan_id, version, *, reviewer_id):
    """Record a trusted review decision. Caller authenticates the engineer."""
    validate_plan_key(project_id, plan_id, version)
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise PlacementApplicationError("An authenticated reviewer ID is required")
    with application_transaction() as (connection, _):
        _, validation = load_plan(connection, project_id, plan_id, version)
        if validation.get("valid") is not True or validation.get("errors"):
            raise PlacementApplicationError("Only a placement plan that passed validation can be approved")
        latest = connection.execute(
            """SELECT MAX(version) FROM placement_plan_versions
               WHERE project_id = ? AND plan_id = ?""",
            (project_id, plan_id),
        ).fetchone()[0]
        if latest != version:
            raise PlacementApplicationError("A newer draft exists. Review the latest version.")
        connection.execute(
            """INSERT INTO placement_approvals (project_id, plan_id, version, reviewer_id)
               VALUES (?, ?, ?, ?) ON CONFLICT (project_id, plan_id, version) DO NOTHING""",
            (project_id, plan_id, version, reviewer_id),
        )


def get_model_row(connection, project_id, version=None):
    if version is None:
        return connection.execute(
            """SELECT * FROM project_ifc_versions WHERE project_id = ?
               ORDER BY version DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
    return connection.execute(
        "SELECT * FROM project_ifc_versions WHERE project_id = ? AND version = ?",
        (project_id, version),
    ).fetchone()


def get_applied_placement_plan(project_id, plan_id):
    """Read the applied proposals and current model path from the same DB snapshot."""
    with application_transaction() as (connection, snapshot_dir):
        applied_version = connection.execute(
            """SELECT MAX(plan_version) FROM placement_applications
               WHERE project_id = ? AND plan_id = ?""",
            (project_id, plan_id),
        ).fetchone()[0]
        if applied_version is None:
            raise PlacementApplicationError("This plan has not been applied to IFC")
        proposals, _ = load_plan(connection, project_id, plan_id, applied_version)
        row = get_model_row(connection, project_id)
        if row is None:
            raise PlacementApplicationError("The project's IFC model was not found")
    return proposals, verified_model_path(snapshot_dir, row)


def _sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def verified_model_path(snapshot_dir, row):
    path = snapshot_dir / row["filename"]
    if _sha256(path) != row["sha256"]:
        raise PlacementApplicationError(f"IFC snapshot {row['version']} failed its integrity check")
    return path


def publish_model(connection, snapshot_dir, project_id, version, write, verify):
    """Verify a private file before publishing its immutable path in SQLite."""
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
        # Same directory: the file is fully present before SQLite can reference it.
        os.replace(temporary, snapshot_dir / filename)
        connection.execute(
            "INSERT INTO project_ifc_versions VALUES (?, ?, ?, ?)",
            (project_id, version, filename, digest),
        )
    finally:
        temporary.unlink(missing_ok=True)


def register_project_ifc(project_id, source_path):
    """Copy the architectural IFC once as version 0, through the trusted backend."""
    from ifc_engine.writer import open_model

    validate_plan_key(project_id, "baseline", 1)
    source = Path(source_path)
    with application_transaction() as (connection, snapshot_dir):
        if get_model_row(connection, project_id) is not None:
            raise PlacementApplicationError("This project already has an IFC baseline")
        publish_model(
            connection, snapshot_dir, project_id, 0,
            lambda path: shutil.copyfile(source, path), open_model,
        )


def get_project_ifc_path(project_id, version=None):
    """Return the verified IFC snapshot path for viewing or export."""
    path = ProjectStore().db_path.resolve()
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_ifc_versions'"
            ).fetchone()
            if table is not None:
                row = get_model_row(connection, project_id, version)
                if row is not None:
                    return verified_model_path(path.with_name(path.name + ".ifc"), row)
    raise PlacementApplicationError("Project IFC version was not found")
