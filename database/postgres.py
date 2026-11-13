"""Read-only PostgreSQL connection for Playbook lookups."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def connect_from_env() -> Any:
    """Connect using DATABASE_URL from the process environment or project .env."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "DATABASE_URL":
                database_url = value.strip().strip("\"'")
                break
    if not database_url:
        raise RuntimeError("DATABASE_URL chưa được cấu hình trong môi trường hoặc file .env.")

    import psycopg2

    connection = psycopg2.connect(database_url, connect_timeout=10, sslmode="require")
    connection.set_session(readonly=True)
    return connection
