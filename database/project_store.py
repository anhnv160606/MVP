"""SQLite persistence for project design state."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4


_DEFAULT_PATH = Path(__file__).with_name("project.db")
_PENDING_REVIEW = "pending_review"
_CREATE_PLACEMENT_VERSIONS = """
CREATE TABLE IF NOT EXISTS placement_plan_versions (
    project_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    status TEXT NOT NULL,
    proposals TEXT NOT NULL,
    validation_result TEXT NOT NULL,
    diff_summary TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (project_id, plan_id, version)
)
"""


class PlacementPlanNotFound(LookupError):
    """The plan does not exist in the requested project."""


class PlacementVersionConflict(ValueError):
    """The plan has changed since the caller loaded it."""


def _encode(value):
    return json.dumps(value, allow_nan=False, sort_keys=True)


def placement_diff(previous, current):
    before = {item["proposed_id"]: _encode(item) for item in previous}
    after = {item["proposed_id"]: _encode(item) for item in current}
    shared = before.keys() & after.keys()
    updated = sorted(item_id for item_id in shared if before[item_id] != after[item_id])
    return {
        "added": sorted(after.keys() - before.keys()),
        "updated": updated,
        "removed": sorted(before.keys() - after.keys()),
        "unchanged": sorted(shared - set(updated)),
    }


class ProjectStore:
    def __init__(self, db_path=None):
        configured_path = os.environ.get("MVP_PROJECT_DB_PATH") or _DEFAULT_PATH
        self.db_path = Path(configured_path if db_path is None else db_path)

    def save_placement_plan(
        self, project_id, proposals, validation_result, *, plan_id, expected_version
    ):
        """Append a draft snapshot in one transaction."""
        try:
            proposals_json = _encode(proposals)
            validation_json = _encode(validation_result)
        except (TypeError, ValueError, RecursionError) as error:
            raise ValueError("proposals and validation_result must be JSON-compatible") from error
        snapshot = json.loads(proposals_json)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            # Lock before reading the previous version so concurrent saves cannot
            # both append edits based on the same snapshot.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_CREATE_PLACEMENT_VERSIONS)
            version = 1
            previous = []
            if plan_id is None:
                plan_id = str(uuid4())
            else:
                row = connection.execute(
                    """
                    SELECT version, proposals FROM placement_plan_versions
                    WHERE project_id = ? AND plan_id = ?
                    ORDER BY version DESC LIMIT 1
                    """,
                    (project_id, plan_id),
                ).fetchone()
                if row is None:
                    raise PlacementPlanNotFound("Placement plan not found in this project")
                latest_version, previous_json = row
                if latest_version != expected_version:
                    raise PlacementVersionConflict(
                        f"Expected version {expected_version}, current version is {latest_version}"
                    )
                version = latest_version + 1
                previous = json.loads(previous_json)

            diff_summary = placement_diff(previous, snapshot)
            connection.execute(
                """
                INSERT INTO placement_plan_versions
                    (project_id, plan_id, version, status, proposals, validation_result, diff_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id, plan_id, version, _PENDING_REVIEW,
                    proposals_json, validation_json, _encode(diff_summary),
                ),
            )

        return {
            "plan_id": plan_id,
            "version": version,
            "status": _PENDING_REVIEW,
            "diff_summary": diff_summary,
        }

    def get_placement_plan(self, project_id, plan_id, version=None):
        """Return the latest or requested snapshot, or None when absent."""
        if not self.db_path.is_file():
            return None
        read_uri = self.db_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(read_uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'placement_plan_versions'"
            ).fetchone()
            if table is None:
                return None
            query = "SELECT * FROM placement_plan_versions WHERE project_id = ? AND plan_id = ?"
            params = (project_id, plan_id)
            if version is not None:
                query += " AND version = ?"
                params += (version,)
            row = connection.execute(query + " ORDER BY version DESC LIMIT 1", params).fetchone()
        if row is None:
            return None
        snapshot = dict(row)
        for field in ("proposals", "validation_result", "diff_summary"):
            snapshot[field] = json.loads(snapshot[field])
        return snapshot
