"""Project-scoped persistence; all writes are transactional Supabase RPCs."""

from postgrest.exceptions import APIError

from tools.panel_contracts import PanelDesignContext


class PanelConflict(ValueError):
    """Reload the current revision; do not retry with a new request ID blindly."""


class PanelStore:
    def __init__(self, client, project_id: str):
        self.client, self.project_id = client, project_id

    def _rpc(self, name, **values):
        try:
            result = (
                self.client.rpc(name, {"p_project_id": self.project_id, **values}).execute().data
            )
        except APIError as error:
            if error.code == "40001":
                raise PanelConflict("Source revisions changed; reload the panel context") from error
            raise
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected response from {name}")
        if result.get("conflict"):
            raise PanelConflict(result["conflict"])
        return result

    def register_context(self, context: PanelDesignContext):
        """Backend-only: pin a fully resolved context after approved IFC application.

        Dev A supplies normalized devices/catalog and all source revisions. A new
        context invalidates the old one. This function is not an agent tool.
        """
        if context.project_id != self.project_id:
            raise ValueError("Context belongs to another project")
        return self._rpc("panel_register_context", p_context=context.model_dump())

    def load_context(self, design_context_id: str) -> PanelDesignContext:
        data = self._rpc("panel_load_context", p_context_id=design_context_id)
        return PanelDesignContext.model_validate(data)

    def get(self, plan_id=None, version=None):
        query = (
            self.client.table("phien_ban_phuong_an_tu_dien")
            .select("*")
            .eq("project_id", self.project_id)
        )
        if plan_id is not None:
            query = query.eq("plan_id", plan_id)
        if version is not None:
            query = query.eq("version", version)
        rows = query.order("version", desc=True).limit(1).execute().data
        return rows[0] if rows else None

    def request(self, request_id):
        rows = (
            self.client.table("phien_ban_phuong_an_tu_dien")
            .select("*")
            .eq("project_id", self.project_id)
            .eq("request_id", request_id)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None

    def save(self, row, expected_version):
        return self._rpc("panel_save_revision", p_row=row, p_expected_version=expected_version)

    def save_render(self, plan_id, version, schedule_hash, status, artifact):
        return self._rpc(
            "panel_set_render",
            p_plan_id=plan_id,
            p_version=version,
            p_schedule_hash=schedule_hash,
            p_status=status,
            p_artifact=artifact,
        )
