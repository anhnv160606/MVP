"""PostgreSQL persistence for project design state."""

import json
from datetime import timezone
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb
from supabase import Client

from database.postgres import project_transaction


_PENDING_REVIEW = "pending_review"


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
    def save_placement_plan(
        self, project_id, proposals, validation_result, *, plan_id, expected_version, removal=None
    ):
        """Append a draft snapshot in one transaction."""
        try:
            proposals_json = _encode(proposals)
            validation_json = _encode(validation_result)
        except (TypeError, ValueError, RecursionError) as error:
            raise ValueError("proposals and validation_result must be JSON-compatible") from error
        snapshot = json.loads(proposals_json)

        with project_transaction(lock_project=project_id) as connection:
            version = 1
            previous = []
            if plan_id is None:
                plan_id = str(uuid4())
            else:
                row = connection.execute(
                    """
                    SELECT version, proposals FROM placement_plan_versions
                    WHERE project_id = %s AND plan_id = %s
                    ORDER BY version DESC LIMIT 1
                    """,
                    (project_id, plan_id),
                ).fetchone()
                if row is None:
                    raise PlacementPlanNotFound("Placement plan not found in this project")
                latest_version = row["version"]
                if latest_version != expected_version:
                    raise PlacementVersionConflict(
                        f"Expected version {expected_version}, current version is {latest_version}"
                    )
                version = latest_version + 1
                previous = row["proposals"]

            diff_summary = placement_diff(previous, snapshot)
            connection.execute(
                """
                INSERT INTO placement_plan_versions
                    (project_id, plan_id, version, status, proposals, validation_result, diff_summary)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    project_id, plan_id, version, _PENDING_REVIEW,
                    Jsonb(snapshot, dumps=_encode), Jsonb(json.loads(validation_json), dumps=_encode),
                    Jsonb(diff_summary, dumps=_encode),
                ),
            )
            if removal is not None:
                if (
                    diff_summary["removed"] != [removal["proposed_id"]]
                    or diff_summary["added"] or diff_summary["updated"]
                ):
                    raise ValueError("Removal audit must match the only device change in the revision")
                connection.execute(
                    """INSERT INTO placement_removals
                       (project_id, plan_id, version, proposed_id, device_id, target, reason, affected_references)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        project_id, plan_id, version, removal["proposed_id"], removal["device_id"],
                        removal["target"], removal["reason"], Jsonb(removal["affected_references"], dumps=_encode),
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
        query = "SELECT * FROM placement_plan_versions WHERE project_id = %s AND plan_id = %s"
        params = (project_id, plan_id)
        if version is not None:
            query += " AND version = %s"
            params += (version,)
        with project_transaction() as connection:
            row = connection.execute(query + " ORDER BY version DESC LIMIT 1", params).fetchone()
        return _format_snapshot(row)

    def get_placement_removal(self, project_id, plan_id, version):
        """Return the deletion request recorded for a specific draft revision."""
        with project_transaction() as connection:
            row = connection.execute(
                """SELECT * FROM placement_removals
                   WHERE project_id = %s AND plan_id = %s AND version = %s""",
                (project_id, plan_id, version),
            ).fetchone()
        return _format_snapshot(row)


def _format_snapshot(row):
    if row is not None:
        row["created_at"] = row["created_at"].astimezone(timezone.utc).isoformat(
            timespec="milliseconds",
        ).replace("+00:00", "Z")
    return row


def create_project(client: Client, name: str) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("Tên project không được rỗng.")

    response = (
        client.table("projects")
        .insert({"name": name})
        .execute()
    )
    return response.data[0]

def create_file_record(
    client: Client,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    response = (
        client.table("project_files")
        .insert(metadata)
        .execute()
    )
    return response.data[0]

def set_file_status(
    client: Client,
    file_id: str,
    status: str,
) -> None:
    if status not in {"pending", "ready", "failed"}:
        raise ValueError("Trạng thái file không hợp lệ.")

    (
        client.table("project_files")
        .update({"status": status})
        .eq("id", file_id)
        .execute()
    )

def mark_ifc_loaded(client: Client, project_id: str) -> None:
    (
        client.table("projects")
        .update({"phase": "IFC_LOADED"})
        .eq("id", project_id)
        .execute()
    )

def get_file_record(
    client: Client,
    project_id: str,
    file_id: str,
) -> dict[str, Any]:
    response = (
        client.table("project_files")
        .select("*")
        .eq("project_id", project_id)
        .eq("id", file_id)
        .single()
        .execute()
    )
    return response.data
