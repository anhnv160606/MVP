from config.settings import Settings

from .base import BaseLLM


def create_llm(
    provider: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    *,
    settings: Settings | None = None,
) -> BaseLLM:
    """Ưu tiên tham số truyền vào, sau đó đến Settings (.env/environment).

    Giữ tương thích với create_llm(provider, model, system_prompt) của dự án cũ.
    """
    settings = settings if settings is not None else Settings()
    provider = (provider if provider is not None else settings.llm_provider).strip().lower()
    key_fields = {
        "groq": "groq_api_key",
        "openai": "openrouter_api_key",
        "gemini": "gemini_api_key",
    }
    if provider not in key_fields:
        raise ValueError(f"Unsupported LLM provider: {provider}")

    selected_model = model if model is not None else settings.llm_model
    if not selected_model or not selected_model.strip():
        raise ValueError("Cần truyền model hoặc cấu hình LLM_MODEL.")

    key_field = key_fields[provider]
    api_key = getattr(settings, key_field)
    if api_key is None or not api_key.get_secret_value().strip():
        raise ValueError(f"Cần cấu hình {key_field.upper()} cho provider {provider}.")

    kwargs = {
        "api_key": api_key.get_secret_value(),
        "model": selected_model.strip(),
        "system_prompt": system_prompt,
    }

    # Chỉ import SDK của provider được chọn sau khi kiểm tra cấu hình.
    if provider == "groq":
        from .groq_provider import GroqProvider

        return GroqProvider(**kwargs)

    if provider == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(**kwargs)

    from .gemini_provider import GeminiProvider

    return GeminiProvider(**kwargs)
