"""Environment configuration shared by CLI, API and LLM providers."""

import base64
import json
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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
    llm_provider: Literal["openai", "groq", "gemini"] = "openai"
    llm_model: str | None = None
    openrouter_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    state_db: str = "data/runtime.sqlite3"
    workspace_dir: str = "data/projects"
    playbook_path: str | None = None
    max_agent_rounds: int = Field(default=24, ge=1, le=100)
    api_token: SecretStr | None = None
    enable_ifcmcp: bool = False

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

    @property
    def supabase_server_key_configured(self) -> bool:
        """Recognize privileged server-key formats without exposing their contents.

        This is a local format check, not token verification or a network probe.
        Public keys can still be used by standalone read-only Playbook clients.
        """
        if not self.supabase_configured:
            return False
        key = self.supabase_key.get_secret_value().strip()
        if key.startswith("sb_secret_"):
            return True
        if key.count(".") == 2:
            try:
                payload = key.split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                return isinstance(claims, dict) and claims.get("role") == "service_role"
            except (ValueError, UnicodeDecodeError):
                return False
        return False


if __name__ == "__main__":
    settings = Settings()
    print(settings.supabase_configured)
