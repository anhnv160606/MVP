"""Read-only, per-model adapter to the official IfcOpenShell IfcMCP library.

No session lifecycle, edit, shape or quantify tools are exposed to the LLM.
IfcMCP is optional because upstream is currently distributed from source.
"""

from pathlib import Path

READ_TOOLS = {
    "ifc_summary",
    "ifc_tree",
    "ifc_info",
    "ifc_select",
    "ifc_relations",
    "ifc_contexts",
    "ifc_materials",
    "ifc_schema",
    "ifc_clash",
    "ifc_validate",
}


class IfcMCPReader:
    """Own an isolated upstream session bound to exactly one project model."""

    def __init__(self, path: str | Path):
        try:
            from ifcmcp.core import IfcSession
        except ImportError as error:
            raise RuntimeError(
                "Install the ifcmcp optional extra; see docs/backend-pipeline.md"
            ) from error
        self.session = IfcSession()
        self.session.ifc_load(str(Path(path).resolve()))

    def call(self, name: str, **arguments):
        """Execute an allowlisted query; STEP IDs apply only to this model revision."""
        if name not in READ_TOOLS:
            raise ValueError(f"IfcMCP operation is not a permitted read: {name}")
        return getattr(self.session, name)(**arguments)
