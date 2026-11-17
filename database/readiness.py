"""Read-only deployment checks; never print credentials or create remote data."""

from config.settings import Settings
from database.supabase_client import get_supabase_client

REQUIRED_TABLES = {
    "projects": "id,name,phase",
    "project_files": "id,project_id,file_kind,original_name,bucket,object_path,size_bytes,sha256,ifc_schema,status",
    "phase1_questionnaire_revisions": "project_id,revision,snapshot",
    "phase1_custom_devices": "project_id,request_id,load_class_id,payload,item",
    "phien_ban_phuong_an_bo_tri": "id,project_id,plan_id,version,source_file_id,output_file_id,proposals,validation_result,review_status,reviewed_by,reviewed_at,applied_at,diff_summary,removal_details,created_at",
}


def check_backend(settings: Settings, *, client=None) -> dict:
    """Verify server-key format, visible schema columns and bucket access.

    A successful check does not test INSERT grants, triggers or file upload.
    """
    if not settings.supabase_server_key_configured:
        return {
            "status": "incomplete",
            "checks": [
                {
                    "resource": "SUPABASE_KEY",
                    "status": "failed",
                    "message": "Use a backend sb_secret_ key or legacy service_role JWT, not a publishable/anon key.",
                }
            ],
        }
    client = client or get_supabase_client(settings)
    checks = []
    for table, columns in REQUIRED_TABLES.items():
        try:
            client.table(table).select(columns).limit(0).execute()
            checks.append({"resource": table, "status": "ok"})
        except Exception as error:
            checks.append(
                {
                    "resource": table,
                    "status": "failed",
                    "error_code": getattr(error, "code", type(error).__name__),
                    "message": "Verify migrations, schema cache and service_role grants.",
                }
            )
    try:
        client.storage.get_bucket("ifc-files")
        checks.append({"resource": "ifc-files", "status": "ok"})
    except Exception as error:
        checks.append(
            {
                "resource": "ifc-files",
                "status": "failed",
                "error_code": getattr(error, "code", type(error).__name__),
                "message": "Verify the private bucket exists and the server key can access it.",
            }
        )
    return {
        "status": "ok" if all(c["status"] == "ok" for c in checks) else "incomplete",
        "scope": "read_only; database writes and upload are not tested",
        "checks": checks,
    }
