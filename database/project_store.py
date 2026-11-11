"""Small SQLite project-state store for questionnaire answers and reviewable drafts.

The playbook remains in Supabase. Project state is kept separately, as in the
architecture document; set PROJECT_STATE_DB to choose a persistent location.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


class ProjectStore:
    def __init__(self, database_path: str | Path | None = None):
        configured = database_path or os.getenv("PROJECT_STATE_DB")
        resolved = Path(configured) if configured else Path(__file__).resolve().parent / "project_state.db"
        if not resolved.is_absolute() and str(resolved) != ":memory:":
            resolved = Path(__file__).resolve().parents[1] / resolved
        self.database_path = str(resolved)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS questionnaire_answers (
                    project_id TEXT PRIMARY KEY, answers_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS placement_plans (
                    plan_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, version INTEGER NOT NULL,
                    status TEXT NOT NULL, proposals_json TEXT NOT NULL, validation_json TEXT NOT NULL,
                    diff_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(project_id, version)
                );
                CREATE INDEX IF NOT EXISTS placement_plans_project_idx ON placement_plans(project_id, version DESC);
            """)

    def save_questionnaire(self, project_id: str, answers: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO questionnaire_answers(project_id, answers_json) VALUES(?, ?) "
                       "ON CONFLICT(project_id) DO UPDATE SET answers_json=excluded.answers_json, updated_at=CURRENT_TIMESTAMP",
                       (project_id, json.dumps(answers, ensure_ascii=False)))

    def get_questionnaire(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT answers_json FROM questionnaire_answers WHERE project_id=?", (project_id,)).fetchone()
        return json.loads(row["answers_json"]) if row else None

    def save_plan(self, project_id: str, proposals: list[dict[str, Any]], validation_result: dict[str, Any],
                  status: str = "pending_review", operation_notes: dict[str, Any] | None = None) -> dict[str, Any]:
        if not project_id:
            raise ValueError("Cần project_id để lưu bản nháp.")
        if status != "pending_review":
            raise ValueError("Bản nháp mới phải ở trạng thái pending_review.")
        import uuid
        plan_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT plan_id, proposals_json FROM placement_plans WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
            version = db.execute("SELECT COALESCE(MAX(version), 0)+1 AS next_version FROM placement_plans WHERE project_id=?", (project_id,)).fetchone()["next_version"]
            previous = json.loads(latest["proposals_json"]) if latest else []
            old_by_id = {x.get("proposed_id"): x for x in previous if x.get("proposed_id")}
            new_by_id = {x.get("proposed_id"): x for x in proposals if x.get("proposed_id")}
            old_ids, new_ids = set(old_by_id), set(new_by_id)
            diff = {"added": len(new_ids-old_ids), "modified": sum(old_by_id[i] != new_by_id[i] for i in old_ids & new_ids),
                    "removed": len(old_ids-new_ids), "operation_notes": operation_notes or {}}
            db.execute("INSERT INTO placement_plans(plan_id, project_id, version, status, proposals_json, validation_json, diff_json) VALUES(?, ?, ?, ?, ?, ?, ?)",
                       (plan_id, project_id, version, status, json.dumps(proposals, ensure_ascii=False),
                        json.dumps(validation_result, ensure_ascii=False), json.dumps(diff)))
        return {"plan_id": plan_id, "project_id": project_id, "version": version, "status": status, "diff_summary": diff}

    def get_plan(self, project_id: str, plan_id: str | None = None) -> dict[str, Any] | None:
        with self._connect() as db:
            if plan_id:
                row = db.execute("SELECT * FROM placement_plans WHERE project_id=? AND plan_id=?", (project_id, plan_id)).fetchone()
            else:
                row = db.execute("SELECT * FROM placement_plans WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for col, key in (("proposals_json", "proposals"), ("validation_json", "validation_result"), ("diff_json", "diff_summary")):
            result[key] = json.loads(result.pop(col))
        return result

    def get_latest_applied_plan(self, project_id: str, before_version: int | None = None) -> dict[str, Any] | None:
        with self._connect() as db:
            if before_version is None:
                row = db.execute("SELECT * FROM placement_plans WHERE project_id=? AND status='applied' ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
            else:
                row = db.execute("SELECT * FROM placement_plans WHERE project_id=? AND status='applied' AND version<? ORDER BY version DESC LIMIT 1",
                                 (project_id, before_version)).fetchone()
        if not row:
            return None
        result = dict(row)
        for col, key in (("proposals_json", "proposals"), ("validation_json", "validation_result"), ("diff_json", "diff_summary")):
            result[key] = json.loads(result.pop(col))
        return result

    def set_plan_status(self, project_id: str, plan_id: str, expected: str, status: str) -> bool:
        allowed = {"pending_review": {"approved", "rejected"}, "approved": {"applied"}, "rejected": set(), "applied": set()}
        if status not in allowed.get(expected, set()):
            raise ValueError(f"Chuyển trạng thái không hợp lệ: {expected} -> {status}")
        with self._connect() as db:
            cursor = db.execute("UPDATE placement_plans SET status=? WHERE project_id=? AND plan_id=? AND status=?",
                                (status, project_id, plan_id, expected))
            return cursor.rowcount == 1

    def approve_plan(self, project_id: str, plan_id: str, expected_version: int) -> dict[str, Any]:
        """Human-review entry point; call only after an authenticated reviewer acts."""
        plan = self.get_plan(project_id, plan_id)
        if not plan or plan["version"] != expected_version:
            raise ValueError("Plan/version không tồn tại hoặc đã thay đổi.")
        if plan["validation_result"].get("errors") or not plan["validation_result"].get("valid", False):
            raise ValueError("Không thể duyệt phương án chưa vượt qua kiểm định.")
        if not self.set_plan_status(project_id, plan_id, "pending_review", "approved"):
            raise ValueError("Phương án không còn ở trạng thái chờ duyệt.")
        return {"plan_id": plan_id, "version": expected_version, "status": "approved"}

    def is_plan_approved(self, project_id: str, plan_id: str, version: int) -> bool:
        plan = self.get_plan(project_id, plan_id)
        return bool(plan and plan["version"] == version and plan["status"] == "approved")
