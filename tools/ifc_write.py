"""Tool 10–13: lưu, duyệt và áp dụng phương án bố trí thiết bị điện."""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import UUID, uuid4, uuid5

from httpx import HTTPError
from postgrest.exceptions import APIError
from storage3.exceptions import StorageException

from config.settings import Settings
from database.ifc_storage import download_ifc, upload_ifc
from database.project_store import create_file_record, get_file_record, set_file_status
from database.supabase_client import get_supabase_client
from ifc_engine import writer
from tools.ifc_read import ifc_list_placed_devices
from tools.validation import engine_validate_placements

_REVISIONS = "phien_ban_phuong_an_bo_tri"
_IFC_BUCKET = "ifc-files"
_MAX_VERSION = 2**63 - 1
_WRITE_LOCK = Lock()
_CLIENT_CONTEXT = ContextVar("placement_storage_client", default=None)
_TYPE_FIELDS = {
    "ifc_class",
    "module_id",
    "geometry_ref",
    "dimensions_m",
    "specs",
    "rated_power",
    "rated_power_W",
    "load_class_id",
    "terminal_class_id",
    "connection_specs",
    "voltage",
    "phases",
}
_EDITABLE_FIELDS = _TYPE_FIELDS | {
    "device_type",
    "space_id",
    "host_id",
    "position",
    "orientation",
    "rule_ids",
    "properties",
    "reason",
    "requirement_id",
    "requirement_unit",
}


class PlacementError(ValueError):
    """Dữ liệu phương án hoặc trạng thái lưu không hợp lệ."""


class PlacementConflict(PlacementError):
    """Phiên bản phương án hoặc IFC đã thay đổi trong lúc thao tác."""


class PlacementDatabaseError(RuntimeError):
    """Thao tác với Supabase thất bại."""


class _ValidationUnavailable(PlacementError):
    """Tool 9 không thể kiểm tra phiên bản đang sửa."""


@dataclass(frozen=True)
class _ReviewContext:
    project_id: str
    plan_id: str
    version: int
    rules: dict


_REVIEW_CONTEXT: ContextVar[_ReviewContext | None] = ContextVar(
    "placement_review_context", default=None
)


def _uuid(value, field):
    try:
        if isinstance(value, (str, UUID)):
            return str(UUID(str(value)))
    except ValueError:
        pass
    raise PlacementError(f"{field} phải là UUID hợp lệ.")


def _version(value):
    if type(value) is not int or not 1 <= value <= _MAX_VERSION:
        raise PlacementError("version phải là số nguyên dương 64 bit.")
    return value


def _identifier(value, field):
    if not isinstance(value, str) or not value.strip() or value != value.strip() or "\0" in value:
        raise PlacementError(f"{field} phải là mã hợp lệ, không có khoảng trắng ở hai đầu.")


def _json_text(value):
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError, OverflowError) as error:
        raise PlacementError("Dữ liệu phương án phải ghi được dưới dạng JSON.") from error


def _json_copy(value):
    return json.loads(_json_text(value))


@contextmanager
def placement_review_context(project_id, plan_id, version, *, rules=None):
    """Gắn phương án đang xem vào lượt gọi tool sửa hoặc xóa.

    Backend gọi sau khi kiểm tra quyền dự án. Ngữ cảnh chỉ tồn tại trong
    khối with và giữ đúng version người dùng đang xem.
    """
    if rules is not None and not isinstance(rules, dict):
        raise PlacementError("rules phải là đối tượng JSON.")
    context = _ReviewContext(
        _uuid(project_id, "project_id"),
        _uuid(plan_id, "plan_id"),
        _version(version),
        _json_copy(rules or {}),
    )
    token = _REVIEW_CONTEXT.set(context)
    try:
        yield
    finally:
        _REVIEW_CONTEXT.reset(token)


def _client():
    return _CLIENT_CONTEXT.get() or get_supabase_client(Settings())


@contextmanager
def placement_storage_context(client):
    """Bind the backend client independently of LLM arguments and global settings."""
    token = _CLIENT_CONTEXT.set(client)
    try:
        yield
    finally:
        _CLIENT_CONTEXT.reset(token)


@contextmanager
def _supabase_request(action):
    try:
        yield
    except APIError as error:
        if error.code in {"42P01", "PGRST205"}:
            raise PlacementDatabaseError(
                f"{action}: kiểm tra bảng đã được tạo và mở qua Data API."
            ) from error
        if error.code == "42501":
            raise PlacementDatabaseError(f"{action}: backend thiếu quyền Supabase.") from error
        raise PlacementDatabaseError(f"{action}: yêu cầu Data API thất bại.") from error
    except (HTTPError, StorageException) as error:
        raise PlacementDatabaseError(
            f"{action}: không hoàn thành được yêu cầu Supabase."
        ) from error


def _prepare_proposals(proposals, plan_id, previous=()):
    if not isinstance(proposals, list):
        raise PlacementError("proposals phải là danh sách.")
    previous_ids = {item["proposed_id"]: item.get("device_id") for item in previous}
    prepared = _json_copy(proposals)
    seen_proposals = set()
    seen_devices = set()
    for index, proposal in enumerate(prepared):
        if not isinstance(proposal, dict):
            raise PlacementError(f"proposals[{index}] phải là đối tượng JSON.")
        for field in ("proposed_id", "device_type", "space_id"):
            _identifier(proposal.get(field), f"proposals[{index}].{field}")

        proposed_id = proposal["proposed_id"]
        if proposed_id in seen_proposals:
            raise PlacementError(f"proposed_id bị trùng: {proposed_id}.")
        seen_proposals.add(proposed_id)
        device_id = previous_ids.get(proposed_id) or str(uuid5(UUID(plan_id), proposed_id))
        if (
            proposal.get("device_id") is not None
            and _uuid(proposal["device_id"], "device_id") != device_id
        ):
            raise PlacementError(f"device_id không khớp với thiết bị đề xuất {proposed_id}.")
        if device_id in seen_devices:
            raise PlacementError(f"device_id bị trùng: {device_id}.")
        seen_devices.add(device_id)
        proposal["device_id"] = device_id

        xyz = writer.coordinates(proposal.get("position"), f"proposals[{index}].position")
        proposal["position"] = dict(zip(("x", "y", "z"), xyz))
        if proposal.get("host_id") is not None:
            _identifier(proposal["host_id"], f"proposals[{index}].host_id")
        rule_ids = proposal.get("rule_ids", [])
        if not isinstance(rule_ids, list):
            raise PlacementError("rule_ids phải là danh sách.")
        for rule_id in rule_ids:
            _identifier(rule_id, "rule_id")
        properties = proposal.get("properties", {})
        if not isinstance(properties, dict):
            raise PlacementError("properties phải là đối tượng JSON.")
        for key in properties:
            _identifier(key, "tên thuộc tính")
    return prepared


def _validate_result(result):
    if not isinstance(result, dict) or type(result.get("valid")) is not bool:
        raise PlacementError("validation_result phải có trường valid kiểu boolean.")
    for field in ("errors", "warnings", "suggested_fixes"):
        if not isinstance(result.get(field), list):
            raise PlacementError(f"validation_result.{field} phải là danh sách.")
    if result["valid"] and result["errors"]:
        raise PlacementError("Kết quả kiểm tra đạt phải có danh sách lỗi rỗng.")


def _placement_diff(previous, current):
    before = {item["proposed_id"]: item for item in previous}
    after = {item["proposed_id"]: item for item in current}
    shared = before.keys() & after.keys()
    updated = {key for key in shared if _json_text(before[key]) != _json_text(after[key])}
    return {
        "added": sorted(after.keys() - before.keys()),
        "updated": sorted(updated),
        "removed": sorted(before.keys() - after.keys()),
        "unchanged": sorted(shared - updated),
    }


def _plan_row(client, project_id, plan_id, version=None):
    with _supabase_request("Đọc phiên bản phương án"):
        query = (
            client.table(_REVISIONS).select("*").eq("project_id", project_id).eq("plan_id", plan_id)
        )
        if version is None:
            query = query.order("version", desc=True)
        else:
            query = query.eq("version", version)
        rows = query.limit(1).execute().data
    return rows[0] if rows else None


def _require_plan(client, project_id, plan_id, version=None):
    row = _plan_row(client, project_id, plan_id, version)
    if row is None:
        raise PlacementError("Không tìm thấy phiên bản phương án trong dự án.")
    return row


def _check_version(row, expected):
    if row["version"] != expected:
        raise PlacementConflict(
            f"Bạn đang xem phiên bản {expected}, phiên bản mới nhất là {row['version']}."
        )


def _save_revision(
    client,
    project_id,
    plan_id,
    version,
    source_file_id,
    previous,
    proposals,
    validation,
    *,
    removal=None,
):
    _version(version)
    diff = _placement_diff(previous, proposals)
    values = {
        "project_id": project_id,
        "plan_id": plan_id,
        "version": version,
        "proposals": proposals,
        "validation_result": validation,
        "diff_summary": diff,
        "removal_details": removal,
    }
    with _WRITE_LOCK:
        values["source_file_id"] = _require_current_file(client, project_id, source_file_id)["id"]
        with _supabase_request("Lưu phiên bản phương án"):
            try:
                client.table(_REVISIONS).insert(values).execute()
            except APIError as error:
                if error.code == "23505":
                    raise PlacementConflict(
                        "Phiên bản đã được lưu bởi thao tác khác. Hãy tải lại phương án."
                    ) from error
                raise
    return {
        "plan_id": plan_id,
        "version": version,
        "status": "pending_review",
        "diff_summary": diff,
    }


def _applied_row(client, project_id, plan_id=None):
    columns = "output_file_id" if plan_id is None else "proposals"
    with _supabase_request("Đọc phiên bản IFC đã áp dụng"):
        query = (
            client.table(_REVISIONS)
            .select(columns)
            .eq("project_id", project_id)
            .eq("review_status", "approved")
            .filter("applied_at", "not.is", "null")
        )
        if plan_id is None:
            query = query.order("applied_at", desc=True).order("id", desc=True)
        else:
            query = query.eq("plan_id", plan_id).order("version", desc=True)
        rows = query.limit(1).execute().data
    return rows[0] if rows else None


def _ready_file(client, project_id, file_id, file_kind):
    with _supabase_request("Đọc thông tin tệp IFC"):
        try:
            row = get_file_record(client, project_id, file_id)
        except APIError as error:
            if error.code == "PGRST116":
                raise PlacementError("Không tìm thấy tệp IFC trong dự án.") from error
            raise
    if not row or row["status"] != "ready" or row["file_kind"] != file_kind:
        raise PlacementError(f"Tệp IFC phải có loại {file_kind} và trạng thái ready.")
    return row


def _current_file(client, project_id, initial_file_id=None):
    applied = _applied_row(client, project_id)
    if applied is not None:
        return _ready_file(client, project_id, applied["output_file_id"], "electrical")
    if initial_file_id is not None:
        return _ready_file(client, project_id, initial_file_id, "architecture")
    with _supabase_request("Tìm IFC kiến trúc của dự án"):
        rows = (
            client.table("project_files")
            .select("*")
            .eq("project_id", project_id)
            .eq("file_kind", "architecture")
            .eq("status", "ready")
            .limit(2)
            .execute()
            .data
        )
    if len(rows) != 1:
        raise PlacementError(
            "Cần source_file_id khi dự án chưa có đúng một IFC kiến trúc ở trạng thái ready."
        )
    return rows[0]


def _require_current_file(client, project_id, source_file_id):
    current = _current_file(client, project_id, source_file_id)
    if source_file_id is not None and current["id"] != source_file_id:
        raise PlacementConflict("IFC hiện hành đã đổi. Hãy kiểm tra lại phương án trên IFC mới.")
    return current


def _download_model(client, row, path):
    with _supabase_request("Tải tệp IFC"):
        download_ifc(client, row["bucket"], row["object_path"], path)
    if _digest(path) != row["sha256"] or path.stat().st_size != row["size_bytes"]:
        raise PlacementError("Tệp IFC tải về sai dung lượng hoặc SHA-256.")
    return writer.open_model(path)


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# Tool 10 - draft_save_placements: Lưu bản nháp bố trí thiết bị theo phiên bản.
def draft_save_placements(
    project_id: str,
    proposals: list[dict],
    validation_result: dict,
    *,
    plan_id: str | None = None,
    expected_version: int | None = None,
    source_file_id: str | None = None,
) -> dict:
    """Lưu phương án bố trí điện thành phiên bản nháp chờ duyệt.

    Args:
        project_id: UUID của dự án trong Supabase.
        proposals: Danh sách đề xuất thiết bị, mỗi đề xuất có proposed_id.
        validation_result: Kết quả Tool 9 cho đúng danh sách và tệp IFC đã kiểm tra.
        plan_id: UUID phương án khi lưu tiếp. Bỏ qua để tạo phương án mới.
        expected_version: Phiên bản đang chỉnh, cần khi truyền plan_id.
        source_file_id: UUID IFC đã kiểm tra. Có thể bỏ qua nếu tệp xác định
            được từ phiên bản trước hoặc IFC hiện hành của dự án.

    Returns:
        plan_id, version, status và tóm tắt thay đổi diff_summary.
    """
    project_id = _uuid(project_id, "project_id")
    if source_file_id is not None:
        source_file_id = _uuid(source_file_id, "source_file_id")
    if plan_id is None:
        if expected_version is not None:
            raise PlacementError("Cần plan_id khi truyền expected_version.")
        plan_id = str(uuid4())
        version = 1
    else:
        plan_id = _uuid(plan_id, "plan_id")
        version = _version(_version(expected_version) + 1)

    _validate_result(validation_result)
    validation = _json_copy(validation_result)
    client = _client()
    previous = []
    if version > 1:
        row = _require_plan(client, project_id, plan_id)
        _check_version(row, expected_version)
        previous = row["proposals"]
        source_file_id = source_file_id or row["output_file_id"] or row["source_file_id"]
    proposals = _prepare_proposals(proposals, plan_id, previous)
    return _save_revision(
        client, project_id, plan_id, version, source_file_id, previous, proposals, validation
    )


def get_placement_plan(project_id, plan_id, version=None):
    """Đọc phiên bản phương án để hiển thị trong màn hình duyệt."""
    row = _plan_row(
        _client(),
        _uuid(project_id, "project_id"),
        _uuid(plan_id, "plan_id"),
        None if version is None else _version(version),
    )
    return {**row, "status": row["review_status"]} if row is not None else None


def get_placement_removal(project_id, plan_id, version):
    """Đọc lý do và các tham chiếu của thao tác xóa trong phiên bản."""
    row = get_placement_plan(project_id, plan_id, _version(version))
    if row is None or row["removal_details"] is None:
        return None
    details = row["removal_details"]
    return {
        **details,
        "project_id": row["project_id"],
        "plan_id": row["plan_id"],
        "version": row["version"],
        "created_at": row["created_at"],
    }


def _revision_update(client, row, values):
    return (
        client.table(_REVISIONS)
        .update(values)
        .eq("id", row["id"])
        .eq("project_id", row["project_id"])
        .eq("plan_id", row["plan_id"])
        .eq("version", row["version"])
        .eq("review_status", row["review_status"])
    )


def approve_placement_plan(project_id, plan_id, version, *, reviewer_id):
    """Ghi duyệt đúng phiên bản sau khi backend xác thực quyền kỹ sư."""
    project_id, plan_id = _uuid(project_id, "project_id"), _uuid(plan_id, "plan_id")
    version, reviewer_id = _version(version), _uuid(reviewer_id, "reviewer_id")
    client = _client()
    with _WRITE_LOCK:
        row = _require_plan(client, project_id, plan_id)
        _check_version(row, version)
        validation = row["validation_result"]
        _validate_result(validation)
        if not validation["valid"]:
            raise PlacementError("Chỉ duyệt phương án đã đạt kiểm tra.")
        if row["review_status"] == "approved":
            return
        if row["review_status"] != "pending_review":
            raise PlacementConflict("Phiên bản này đã có quyết định duyệt.")
        _require_current_file(client, project_id, row["source_file_id"])
        validation = _validate_saved_plan(client, row)
        if not validation["valid"]:
            raise PlacementError("Phương án không đạt kiểm tra lại trên IFC hiện hành.")
        values = {
            "review_status": "approved",
            "reviewed_by": reviewer_id,
            "reviewed_at": datetime.now(UTC).isoformat(),
        }
        values["validation_result"] = validation
        with _supabase_request("Ghi quyết định duyệt"):
            updated = _revision_update(client, row, values).execute().data
        if (
            not updated
            and _require_plan(client, project_id, plan_id, version)["review_status"] != "approved"
        ):
            raise PlacementConflict("Trạng thái duyệt đã thay đổi.")


def _specs(project_id, plan_id, proposals):
    return {
        item["proposed_id"]: writer.device_spec(project_id, plan_id, item) for item in proposals
    }


def _upload_electrical(client, project_id, path, directory):
    filename = f"{uuid4().hex}.ifc"
    object_path = f"{project_id}/electrical/{filename}"
    size = path.stat().st_size
    checksum = _digest(path)
    with _supabase_request("Tạo bản ghi IFC điện"):
        file = create_file_record(
            client,
            {
                "project_id": project_id,
                "file_kind": "electrical",
                "original_name": filename,
                "bucket": _IFC_BUCKET,
                "object_path": object_path,
                "size_bytes": size,
                "sha256": checksum,
                "ifc_schema": "IFC4",
                "status": "pending",
            },
        )

    readback = directory / "uploaded.ifc"
    try:
        with _supabase_request("Tải IFC điện lên Storage và đọc lại"):
            upload_ifc(client, _IFC_BUCKET, object_path, path)
            download_ifc(client, _IFC_BUCKET, object_path, readback)
        if readback.stat().st_size != size or _digest(readback) != checksum:
            raise PlacementError("IFC trên Storage sai dung lượng hoặc SHA-256.")
    except (PlacementError, PlacementDatabaseError, OSError) as error:
        try:
            with _supabase_request("Đánh dấu IFC tải lên thất bại"):
                set_file_status(client, file["id"], "failed")
        except PlacementDatabaseError as status_error:
            error.add_note(str(status_error))
        raise

    with _supabase_request("Đánh dấu IFC đã sẵn sàng"):
        set_file_status(client, file["id"], "ready")
    return _ready_file(client, project_id, file["id"], "electrical")["id"]


def _batch_result(status, model_version=None, *, created=(), updated=(), removed=(), errors=()):
    return {
        "status": status,
        "created_ids": list(created),
        "updated_ids": list(updated),
        "removed_ids": list(removed),
        "errors": list(errors),
        "model_version": model_version,
    }


def _replay_result(client, row):
    file_id = _ready_file(client, row["project_id"], row["output_file_id"], "electrical")["id"]
    return _batch_result("already_applied", file_id)


# Tool 11 - ifc_apply_placements_batch: Ghi phương án đã duyệt vào IFC.
def ifc_apply_placements_batch(approved_plan_id: str, version: int, project_id: str) -> dict:
    """Ghi phiên bản phương án đã duyệt vào IFC và lưu kết quả trên Supabase.

    Args:
        approved_plan_id: UUID của phương án.
        version: Số phiên bản đã duyệt.
        project_id: UUID của dự án.

    Returns:
        status, danh sách GlobalId đã tạo/sửa/xóa, errors và model_version
        là UUID bản ghi IFC điện. Gọi lại phiên bản đã ghi trả already_applied.
    """
    try:
        plan_id = _uuid(approved_plan_id, "approved_plan_id")
        project_id, version = _uuid(project_id, "project_id"), _version(version)
        client = _client()
        # Tuần tự hóa lượt ghi trong một tiến trình MVP.
        with _WRITE_LOCK:
            return _apply_approved_revision(client, project_id, plan_id, version)
    except (ValueError, TypeError, OSError, RuntimeError, OverflowError) as error:
        return _batch_result("failed", errors=[str(error)])


def _apply_approved_revision(client, project_id, plan_id, version):
    row = _require_plan(client, project_id, plan_id, version)
    if row["output_file_id"] is not None:
        return _replay_result(client, row)

    _check_version(_require_plan(client, project_id, plan_id), version)
    validation = row["validation_result"]
    _validate_result(validation)
    if row["review_status"] != "approved" or not row["reviewed_by"] or not validation["valid"]:
        raise PlacementError("Phiên bản phải đạt kiểm tra và được kỹ sư duyệt trước khi ghi IFC.")

    source = _require_current_file(client, project_id, row["source_file_id"])
    if not _validate_saved_plan(client, row)["valid"]:
        raise PlacementError("Phương án không đạt kiểm tra lại trước khi ghi IFC.")
    applied = _applied_row(client, project_id, plan_id)
    previous = applied["proposals"] if applied is not None else []
    proposals = row["proposals"]
    diff = _placement_diff(previous, proposals)
    before = _specs(project_id, plan_id, previous)
    current = _specs(project_id, plan_id, proposals)
    removed = [before[key] for key in diff["removed"]]

    with TemporaryDirectory(prefix="mvp-ifc-") as location:
        directory = Path(location)
        model = _download_model(client, source, directory / "source.ifc")
        for key in diff["removed"] + diff["updated"]:
            writer.remove_device(model, before[key])
        for key in diff["added"] + diff["updated"]:
            writer.create_device(model, current[key])

        has_changes = diff["added"] or diff["updated"] or diff["removed"]
        if has_changes or source["file_kind"] == "architecture":
            output = directory / "output.ifc"
            model.write(str(output))
            writer.verify_batch(writer.open_model(output), current, removed)
            file_id = _upload_electrical(client, project_id, output, directory)
        else:
            writer.verify_batch(model, current, removed)
            file_id = source["id"]

    latest = _require_plan(client, project_id, plan_id)
    _check_version(latest, version)
    if latest["output_file_id"] is not None:
        return _replay_result(client, latest)
    _require_current_file(client, project_id, source["id"])
    values = {"output_file_id": file_id, "applied_at": datetime.now(UTC).isoformat()}
    with _supabase_request("Ghi nhận phiên bản IFC đã áp dụng"):
        updated_rows = (
            _revision_update(client, row, values).is_("output_file_id", "null").execute().data
        )
    if not updated_rows:
        recorded = _require_plan(client, project_id, plan_id, version)
        if recorded["output_file_id"] is not None:
            return _replay_result(client, recorded)
        raise PlacementConflict("Trạng thái phiên bản đã đổi trước khi ghi nhận IFC.")
    return _batch_result(
        "applied",
        file_id,
        created=(current[key].guid for key in diff["added"]),
        updated=(current[key].guid for key in diff["updated"]),
        removed=(spec.guid for spec in removed),
    )


def _validate_saved_plan(client, row):
    """Never trust a validation result supplied by an LLM or external caller."""
    source = _require_current_file(client, row["project_id"], row["source_file_id"])
    applied = _applied_row(client, row["project_id"], row["plan_id"])
    previous = applied["proposals"] if applied else []
    managed = {spec.guid for spec in _specs(row["project_id"], row["plan_id"], previous).values()}
    with TemporaryDirectory(prefix="mvp-validation-") as location:
        path = Path(location) / "source.ifc"
        model = _download_model(client, source, path)
        for spec in _specs(row["project_id"], row["plan_id"], row["proposals"]).values():
            writer.resolve_device_mount(model, spec)
        return _run_validator(row["proposals"], path, {}, managed, removing=bool(previous))


def _merge_device(device, changes):
    updated = dict(device)
    if any(
        changes[key] != device.get(key)
        for key in ("device_type", "ifc_class", "module_id")
        if key in changes
    ):
        stale_fields = _TYPE_FIELDS | {"rule_ids"}
        if "device_type" not in changes and "ifc_class" not in changes:
            stale_fields = stale_fields - {"ifc_class"}
        for key in stale_fields:
            updated.pop(key, None)
    if changes.get("space_id", device["space_id"]) != device["space_id"]:
        updated.pop("host_id", None)
        updated.pop("rule_ids", None)
    if changes.get("host_id", device.get("host_id")) != device.get("host_id"):
        updated.pop("rule_ids", None)
    for key, value in changes.items():
        if key == "properties":
            if not isinstance(value, dict):
                raise PlacementError("properties phải là đối tượng JSON.")
            updated[key] = {**updated.get(key, {}), **value}
        elif key in _TYPE_FIELDS and value is None:
            updated.pop(key, None)
        else:
            updated[key] = value
    return updated


def _run_validator(proposals, path, rules, managed_guids, *, removing):
    try:
        # Các thiết bị của cùng phương án đã có trong danh sách đề xuất.
        existing = [
            item
            for item in ifc_list_placed_devices(path)
            if item["element_id"] not in managed_guids
        ]
        result = engine_validate_placements(proposals, {"existing_devices": existing}, rules, path)
        _validate_result(result)
        result = _json_copy(result)
        if (
            removing
            and not proposals
            and result["errors"]
            and all(
                isinstance(error, dict) and error.get("code") == "ERR_EMPTY_PROPOSALS"
                for error in result["errors"]
            )
        ):
            # Danh sách rỗng là kết quả hợp lệ khi người dùng chủ động xóa thiết bị cuối.
            result["errors"] = []
            result["valid"] = True
        return result
    except (ValueError, TypeError, OSError, RuntimeError, KeyError) as error:
        raise _ValidationUnavailable(f"Không chạy được Tool 9: {error}") from error


def _require_review_context():
    context = _REVIEW_CONTEXT.get()
    if context is None:
        raise PlacementError("Backend chưa cung cấp ngữ cảnh phương án đang mở.")
    return context


def _stage_device_change(context, device_id, *, changes=None, reason=None):
    project_id, plan_id, version = context.project_id, context.plan_id, context.version
    client = _client()
    draft = _require_plan(client, project_id, plan_id)
    _check_version(draft, version)

    previous = draft["proposals"]
    candidate = _prepare_proposals(previous, plan_id, previous)
    for index, proposal in enumerate(candidate):
        if proposal["device_id"] == device_id:
            break
    else:
        raise PlacementError("Không tìm thấy thiết bị trong phiên bản phương án đang mở.")

    proposed_id = proposal["proposed_id"]
    updated = None
    if changes is None:
        candidate.pop(index)
    else:
        candidate[index] = _merge_device(proposal, changes)
        candidate = _prepare_proposals(candidate, plan_id, previous)
        updated = candidate[index]

    source = _current_file(client, project_id, draft["source_file_id"])
    applied = _applied_row(client, project_id, plan_id)
    applied_specs = _specs(project_id, plan_id, applied["proposals"]) if applied is not None else {}
    before = applied_specs.get(proposed_id)
    references = []
    with TemporaryDirectory(prefix="mvp-ifc-") as location:
        path = Path(location) / "source.ifc"
        model = _download_model(client, source, path)
        if before is not None:
            if reason is not None or _json_text(updated) != before.payload:
                references = writer.get_removal_references(model, before)
                if any(item["blocks_removal"] for item in references):
                    return "blocked", None, None, references, version
            else:
                writer.verify_device(model, before)
        if updated is not None:
            writer.resolve_device_mount(model, writer.device_spec(project_id, plan_id, updated))
        validation = _run_validator(
            candidate,
            path,
            context.rules,
            {spec.guid for spec in applied_specs.values()},
            removing=reason is not None,
        )

    if not validation["valid"]:
        return "validation_failed", None, validation, references, version

    _check_version(_require_plan(client, project_id, plan_id), version)
    _require_current_file(client, project_id, source["id"])
    if updated is not None and _json_text(candidate) == _json_text(previous):
        return "unchanged", updated, validation, references, version

    removal = None
    if reason is not None:
        removal = {
            "device_id": device_id,
            "proposed_id": proposed_id,
            "reason": reason,
            "affected_references": references,
        }
    saved = _save_revision(
        client,
        project_id,
        plan_id,
        version + 1,
        source["id"],
        previous,
        candidate,
        validation,
        removal=removal,
    )
    return saved["status"], updated, validation, references, saved["version"]


def _edit_failure_status(error):
    if isinstance(error, _ValidationUnavailable):
        return "validation_unavailable"
    if isinstance(error, PlacementConflict):
        return "conflict"
    return "failed"


# Tool 12 - ifc_modify_device: Chỉnh thiết bị và lưu bản nháp sau khi kiểm tra.
def ifc_modify_device(device_id: str, changes: dict) -> dict:
    """Chỉnh thiết bị trong phương án đang mở và kiểm tra lại trước khi lưu.

    Args:
        device_id: UUID của thiết bị trong phương án.
        changes: Các trường cần đổi, ví dụ position, orientation, host_id,
            device_type hoặc properties.

    Returns:
        status, updated_device, validation_result, plan_id và version.
    """
    context = None
    try:
        context = _require_review_context()
        device_id = _uuid(device_id, "device_id")
        if not isinstance(changes, dict) or not changes or changes.keys() - _EDITABLE_FIELDS:
            raise PlacementError("changes phải chứa các trường thiết bị được phép chỉnh sửa.")
        status, updated, validation, _, version = _stage_device_change(
            context,
            device_id,
            changes=_json_copy(changes),
        )
        if status == "blocked":
            validation = {
                "valid": False,
                "errors": [
                    {
                        "code": "blocked",
                        "message": "Thiết bị còn tham chiếu IFC bên ngoài phương án.",
                    }
                ],
                "warnings": [],
                "suggested_fixes": [],
            }
    except (ValueError, TypeError, OSError, RuntimeError, OverflowError) as error:
        status = _edit_failure_status(error)
        validation = {
            "valid": False,
            "errors": [{"code": status, "message": str(error)}],
            "warnings": [],
            "suggested_fixes": [],
        }
        updated, version = None, None
    return {
        "status": status,
        "updated_device": updated,
        "validation_result": validation,
        "plan_id": context.plan_id if context else None,
        "version": version,
    }


# Tool 13 - ifc_remove_device: Kiểm tra tham chiếu và lưu yêu cầu xóa thiết bị.
def ifc_remove_device(device_id: str, reason: str) -> dict:
    """Kiểm tra tham chiếu và lưu yêu cầu xóa thiết bị thành bản nháp mới.

    Args:
        device_id: UUID của thiết bị trong phương án đang mở.
        reason: Lý do xóa được lưu cùng phiên bản mới.

    Returns:
        status, removed_id, affected_references, validation_result, errors,
        plan_id và version. Thiết bị IFC chỉ bị xóa khi áp dụng bản đã duyệt.
    """
    context = None
    removed_id, validation, version = None, None, None
    references, errors = [], []
    try:
        context = _require_review_context()
        device_id = _uuid(device_id, "device_id")
        if not isinstance(reason, str) or not reason.strip() or "\0" in reason:
            raise PlacementError("reason phải là lý do xóa hợp lệ.")
        status, _, validation, references, version = _stage_device_change(
            context, device_id, reason=reason.strip()
        )
        if status == "pending_review":
            removed_id = device_id
        if status == "blocked":
            errors = [
                "Thiết bị còn tham chiếu IFC bên ngoài phương án. Cần xử lý tham chiếu trước khi xóa."
            ]
    except (ValueError, TypeError, OSError, RuntimeError, OverflowError) as error:
        status = _edit_failure_status(error)
        errors = [str(error)]
    return {
        "status": status,
        "removed_id": removed_id,
        "affected_references": references,
        "errors": errors,
        "plan_id": context.plan_id if context else None,
        "version": version,
        "validation_result": validation,
    }
