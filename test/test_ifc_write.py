r"""Chỉ kiểm thử draft_save_placements với database Supabase thật.

Chạy từ thư mục gốc:
    .\.venv\Scripts\python.exe test/test_ifc_write.py --project-id UUID --source-file-id UUID

Có thể truyền --proposals-file duong_dan.json để dùng danh sách đề xuất riêng.
Mỗi lần chạy tạo một phương án mới và giữ lại bản ghi để kiểm chứng.
Không duyệt phương án, không ghi IFC và không gọi Tool 9.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.settings import Settings
from database.supabase_client import get_supabase_client
from tools.ifc_write import draft_save_placements


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-id", required=True, type=UUID, help="UUID dự án có thật trong Supabase"
    )
    parser.add_argument("--source-file-id", type=UUID, help="UUID IFC ready hiện hành của dự án")
    parser.add_argument("--proposals-file", type=Path, help="File JSON chứa danh sách proposals")
    args = parser.parse_args()

    # Đọc .env tại thư mục gốc ngay cả khi chạy script từ thư mục khác.
    settings = Settings(_env_file=ROOT / ".env")
    if not settings.supabase_configured:
        parser.error("Cần SUPABASE_URL và SUPABASE_KEY trong .env tại thư mục gốc.")

    proposals = (
        json.loads(args.proposals_file.read_text(encoding="utf-8-sig"))
        if args.proposals_file
        else [
            {
                "proposed_id": "test-draft-light-001",
                "device_type": "light",
                "space_id": "TEST_ROOM_DRAFT_ONLY",
                "position": {"x": 1.0, "y": 2.0, "z": 2.8},
                "properties": {"test_note": "Kiểm thử lưu nháp vào Supabase thật"},
            }
        ]
    )
    # draft_save_placements tự tạo client bằng Settings(), đọc .env theo cwd.
    os.chdir(ROOT)
    # Đây là test lưu DB, chưa kiểm tra bố trí bằng Tool 9. Đánh dấu chưa đạt
    # để dữ liệu mẫu không bị hiểu nhầm là đủ điều kiện duyệt/ghi IFC.
    validation = {
        "valid": False,
        "errors": [
            {"code": "TEST_NOT_VALIDATED", "message": "Bản nháp kiểm thử lưu DB, chưa chạy Tool 9."}
        ],
        "warnings": [],
        "suggested_fixes": [],
    }
    project_id = str(args.project_id)
    source_file_id = str(args.source_file_id) if args.source_file_id else None
    print("Đang gọi draft_save_placements để tạo bản nháp trong Supabase thật...", flush=True)
    result = draft_save_placements(
        project_id,
        proposals,
        validation,
        source_file_id=source_file_id,
    )
    print("Kết quả lưu (giữ lại để tra cứu nếu bước đọc lại thất bại):", flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    # Đọc trực tiếp DB để xác minh thao tác ghi, không dùng mock.
    client = get_supabase_client(settings)
    row = (
        client.table("phien_ban_phuong_an_bo_tri")
        .select("*")
        .eq("project_id", project_id)
        .eq("plan_id", result["plan_id"])
        .eq("version", result["version"])
        .single()
        .execute()
        .data
    )
    if not row:
        raise RuntimeError("Không đọc lại được bản nháp vừa lưu từ Supabase.")
    expected = {
        "project_id": project_id,
        "plan_id": result["plan_id"],
        "version": 1,
        "review_status": "pending_review",
        "validation_result": validation,
        "diff_summary": result["diff_summary"],
        "output_file_id": None,
    }
    for field, value in expected.items():
        if row.get(field) != value:
            raise RuntimeError(f"Dữ liệu đọc lại không khớp trường {field}.")
    if source_file_id and row["source_file_id"] != source_file_id:
        raise RuntimeError("source_file_id đọc lại không khớp.")
    if len(row["proposals"]) != len(proposals):
        raise RuntimeError("Số lượng proposals đọc lại không khớp.")
    for original, saved in zip(proposals, row["proposals"]):
        if any(saved.get(key) != value for key, value in original.items()):
            raise RuntimeError("Nội dung proposal đọc lại không khớp dữ liệu gửi lên.")
        UUID(saved["device_id"])

    print("PASS: đã lưu và đọc lại đúng bản nháp từ Supabase thật.")
    print("Bảng: phien_ban_phuong_an_bo_tri")
    print(json.dumps(row, ensure_ascii=False, indent=2))
    print("Bản ghi được giữ lại trong DB để bạn kiểm chứng.")


if __name__ == "__main__":
    main()
