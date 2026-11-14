"""Application and questionnaire database configuration."""

from getpass import getpass
import os
from pathlib import Path
import shlex
import sys
from typing import Literal

from pydantic import HttpUrl, SecretStr
try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:
    class BaseSettings:
        pass
    def SettingsConfigDict(**kwargs):
        return kwargs


DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(DOTENV_PATH),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
        case_sensitive=False,
    )

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    supabase_url: HttpUrl | None = None
    supabase_key: SecretStr | None = None

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


def _dotenv_database_url() -> str:
    if not DOTENV_PATH.is_file():
        return ""
    with DOTENV_PATH.open(encoding="utf-8") as dotenv:
        for line in dotenv:
            key, separator, value = line.partition("=")
            if not separator or key.strip().removeprefix("export ").strip() != "DATABASE_URL":
                continue
            try:
                fields = shlex.split(value, comments=True)
            except ValueError:
                raise ValueError("Dòng DATABASE_URL trong .env không hợp lệ.") from None
            if len(fields) > 1:
                raise ValueError("Dòng DATABASE_URL trong .env không hợp lệ.")
            return fields[0].strip() if fields else ""
    return ""


def questionnaire_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip() or _dotenv_database_url()
    if not database_url and sys.stdin.isatty():
        database_url = getpass("Dán chuỗi kết nối PostgreSQL (ẩn ký tự): ").strip()
    if not database_url:
        raise ValueError("Cần cấu hình DATABASE_URL bằng chuỗi kết nối PostgreSQL.")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL phải bắt đầu bằng postgresql:// hoặc postgres://.")
    return database_url
