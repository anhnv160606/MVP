"""Kiểm tra môi trường backend"""

import argparse #phân tích các đối số dòng lệnh
import json
import platform #lấy thông tin hệ điều hành, phiên bản python đang chạy
from config.settings import Settings #load cấu hình từ biến môi trường và file .env
from pathlib import Path #xử lí đường dẫn file theo hướng đối tượng
from pydantic import ValidationError #kiểm tra dữ liệu đầu vào có hợp lệ hay không

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kiểm tra môi trường backend")
    parser.add_argument("command", choices=["check-config"], nargs="?", default="check-config")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Đường dẫn đến file .env")
    parser.add_argument("--require-supabase", action="store_true", help="Yêu cầu cấu hình Supabase hợp lệ")
    args = parser.parse_args(argv)

    if args.env_file and not args.env_file.is_file():
        parser.error(f"File .env không tồn tại: {args.env_file}")

    try:
        settings = (Settings(_env_file=args.env_file) if args.env_file else Settings())

    except ValidationError as e:
        field = sorted({str(error["loc"][0]) for error in e.errors()})
        parser.exit(2, f"Thiếu hoặc không hợp lệ các biến môi trường: {', '.join(field)}\n")

    configured = settings.supabase_configured
    print(
        json.dumps(
            {
                "status": "ok" if configured or not args.require_supabase else "incomplete",
                "python": platform.python_version(),
                "app_env": settings.app_env,
                "log_level": settings.log_level,
                "supabase": {
                    "url_present": settings.supabase_url is not None,
                    "secret_key_present": settings.supabase_key is not None,
                    "configured": configured,
                    "connection": "not_checked",
                },
            },
            indent=2,
        )
    )
    return 0 if configured or not args.require_supabase else 1



        