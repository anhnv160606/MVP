"""Durable local pipeline state with optimistic revision checks."""

import json
import sqlite3
from pathlib import Path


class StateConflict(ValueError):
    """A concurrent request changed the project revision."""


class ProjectMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runtime_projects (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS placement_checkpoints (project_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def checkpoint(self, project_id: str, plan: dict | None = None):
        with self.connect() as connection:
            if plan is not None:
                connection.execute(
                    "INSERT INTO placement_checkpoints VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET payload=excluded.payload",
                    (project_id, json.dumps(plan)),
                )
            row = connection.execute(
                "SELECT payload FROM placement_checkpoints WHERE project_id=?", (project_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, project_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM runtime_projects WHERE id=?", (project_id,)
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return json.loads(row[0])

    def create(self, project_id: str, **values) -> dict:
        state = {**values, "project_id": project_id, "revision": 1, "phase": "IFC_LOADED"}
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO runtime_projects VALUES (?, ?, ?)",
                (project_id, 1, json.dumps(state, allow_nan=False)),
            )
        return state

    def save(self, state: dict, expected_revision: int) -> dict:
        state = {**state, "revision": expected_revision + 1}
        with self.connect() as connection:
            result = connection.execute(
                "UPDATE runtime_projects SET revision=?, payload=? WHERE id=? AND revision=?",
                (
                    state["revision"],
                    json.dumps(state, allow_nan=False),
                    state["project_id"],
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                raise StateConflict("Project state changed; reload before retrying")
        return state
