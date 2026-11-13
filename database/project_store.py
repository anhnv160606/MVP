from typing import Any
from supabase import Client

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

