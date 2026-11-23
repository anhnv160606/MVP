"""Kiểm tra môi trường backend"""

import argparse
import json
import platform
import sys
from pathlib import Path

from pydantic import ValidationError

from config.settings import Settings


def main(argv: list[str] | None = None) -> int:
    # Windows redirected terminals can default to cp1252, which cannot print
    # Vietnamese help/errors. Keep CLI text and JSON consistently UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Kiểm tra môi trường backend")
    parser.add_argument(
        "command",
        choices=["check-config", "check-backend", "serve", "preview"],
        nargs="?",
        default="check-config",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"), help="Đường dẫn đến file .env"
    )
    parser.add_argument(
        "--require-supabase", action="store_true", help="Yêu cầu cấu hình Supabase hợp lệ"
    )
    args = parser.parse_args(argv)

    if args.command == "preview":
        import uvicorn

        from api.frontend import create_preview_app

        uvicorn.run(create_preview_app(), host=args.host, port=args.port)
        return 0

    if args.env_file and not args.env_file.is_file():
        parser.error(f"File .env không tồn tại: {args.env_file}")

    try:
        settings = Settings(_env_file=args.env_file) if args.env_file else Settings()

    except ValidationError as e:
        field = sorted({str(error["loc"][0]) for error in e.errors()})
        parser.exit(2, f"Thiếu hoặc không hợp lệ các biến môi trường: {', '.join(field)}\n")

    if args.command == "check-backend":
        from database.readiness import check_backend

        result = check_backend(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1
    configured = (
        settings.supabase_server_key_configured
        if args.require_supabase
        else settings.supabase_configured
    )
    if args.command == "serve":
        import uvicorn

        from api.app import create_app

        uvicorn.run(
            create_app(settings), host=args.host, port=args.port, log_level=settings.log_level
        )
        return 0
    print(
        json.dumps(
            {
                "status": "ok" if configured or not args.require_supabase else "incomplete",
                "python": platform.python_version(),
                "app_env": settings.app_env,
                "log_level": settings.log_level,
                "llm": {
                    "provider": settings.llm_provider,
                    "model_configured": bool(settings.llm_model),
                },
                "supabase": {
                    "url_present": settings.supabase_url is not None,
                    "secret_key_present": settings.supabase_key is not None,
                    "configured": configured,
                    "server_key_configured": settings.supabase_server_key_configured,
                    "connection": "not_checked",
                },
            },
            indent=2,
        )
    )
    return 0 if configured or not args.require_supabase else 1
