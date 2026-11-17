"""Project IFC upload, state and reviewed IFC download endpoints."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from database.ifc_storage import upload_ifc
from database.project_store import (
    create_file_record,
    create_project,
    mark_ifc_loaded,
    set_file_status,
)
from ifc_engine.reader import read_spaces
from ifc_engine.writer import open_model

log = logging.getLogger(__name__)


def create_project_router() -> APIRouter:
    router = APIRouter(tags=["projects"])

    @router.post("/projects", status_code=201)
    def upload(request: Request, name: str = Form(...), file: UploadFile = File(...)):
        backend, memory = request.app.state.backend, request.app.state.memory
        root = Path(request.app.state.settings.workspace_dir).resolve()
        if not name.strip():
            raise ValueError("Project name cannot be empty")
        with TemporaryDirectory(prefix="mvp-upload-") as location:
            source = Path(location) / "source.ifc"
            with source.open("wb") as output:
                total = 0
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 250 * 1024 * 1024:
                        raise HTTPException(413, "IFC exceeds 250 MiB upload limit")
                    output.write(chunk)
            with source.open("rb") as stream:
                if b"ISO-10303-21;" not in stream.read(256).upper():
                    raise HTTPException(422, "Expected an IFC STEP file header")
            try:
                open_model(source)
                spatial = read_spaces(source)
            except (RuntimeError, OSError) as error:
                raise HTTPException(422, "Cannot parse a supported IFC4 model") from error
            if not spatial.rooms:
                raise ValueError("IFC must include IfcSpace rooms for device placement")
            project = create_project(backend.client, name)
            project_id = project["id"]
            object_path = f"{project_id}/architecture/{uuid4().hex}.ifc"
            row = create_file_record(
                backend.client,
                {
                    "project_id": project_id,
                    "file_kind": "architecture",
                    "original_name": Path(file.filename or "architecture.ifc").name,
                    "bucket": "ifc-files",
                    "object_path": object_path,
                    "size_bytes": total,
                    "sha256": spatial.revision,
                    "ifc_schema": "IFC4",
                    "status": "pending",
                },
            )
            try:
                upload_ifc(backend.client, "ifc-files", object_path, source)
                target = root / project_id / f"{row['id']}.ifc"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
                set_file_status(backend.client, row["id"], "ready")
                state = memory.create(
                    project_id,
                    name=name.strip(),
                    ifc_path=str(target),
                    architecture_path=str(target),
                    architecture_revision=spatial.revision,
                    model_revision=spatial.revision,
                    source_file_id=row["id"],
                    plan=None,
                )
                mark_ifc_loaded(backend.client, project_id)
                return state
            except Exception:
                try:
                    set_file_status(backend.client, row["id"], "failed")
                except Exception as cleanup_error:
                    log.error("Could not mark failed upload: %s", type(cleanup_error).__name__)
                raise

    @router.get("/projects/{project_id}")
    def get_state(project_id: str, request: Request):
        return request.app.state.memory.get(project_id)

    @router.get("/projects/{project_id}/ifc")
    def download(project_id: str, request: Request):
        state = request.app.state.memory.get(project_id)
        return FileResponse(
            state["ifc_path"], media_type="application/x-step", filename=f"{project_id}.ifc"
        )

    return router
