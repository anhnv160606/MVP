"""Persist conversational turns separately from model-specific tool histories."""

import json

from memory.project import ProjectMemory


class SessionMemory:
    def __init__(self, projects: ProjectMemory):
        self.projects = projects
        with projects.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS chat_turns (id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    def append(self, project_id: str, role: str, content: str):
        if role not in {"user", "assistant"}:
            raise ValueError("Only completed conversational turns are persisted")
        with self.projects.connect() as connection:
            connection.execute(
                "INSERT INTO chat_turns(project_id,payload) VALUES (?,?)",
                (project_id, json.dumps({"role": role, "content": content})),
            )

    def history(self, project_id: str, limit: int = 20) -> list[dict]:
        with self.projects.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM chat_turns WHERE project_id=? ORDER BY id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]
