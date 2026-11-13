from pathlib import Path

from supabase import Client


def upload_ifc(
    client: Client,
    bucket: str,
    object_path: str,
    local_path: Path,
) -> None:
    with local_path.open("rb") as file:
        client.storage.from_(bucket).upload(
            path=object_path,
            file=file,
            file_options={
                "content-type": "application/octet-stream",
                "upsert": "false",
            },
        )


def download_ifc(
    client: Client,
    bucket: str,
    object_path: str,
    local_path: Path,
) -> None:
    content = client.storage.from_(bucket).download(object_path)

    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(content)