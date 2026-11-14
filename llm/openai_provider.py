from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from .base import BaseLLM, LLMResponse, ToolCall

log = logging.getLogger(__name__)


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenAIProvider(BaseLLM):
    """Provider cho mọi API tương thích OpenAI (mặc định: OpenRouter).

    Đổi base_url để dùng nhà cung cấp khác (Groq, Ollama, ...).
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        system_prompt: str | None = None,
        base_url: str = OPENROUTER_BASE_URL,
        temperature: float = 0.2,
        max_tool_chars: int = 6000,
        fallback_models: list[str] | None = None,
        max_retries: int = 1,
        default_headers: dict[str, str] | None = None,
    ):
        # max_retries thấp: ở gói free, mỗi lần retry cũng tốn 1 request trong hạn mức ngày.
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
            default_headers=default_headers,  # tùy chọn, ví dụ {"X-Title": "IFC Agent"}
        )
        self.model = model
        self.temperature = temperature
        self.max_tool_chars = max_tool_chars
        self.fallback_models = fallback_models or []
        self.system_prompt = system_prompt

        self.tools: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = self._base_messages()
        self.pending_calls: dict[str, str] = {}

    def _base_messages(self) -> list[dict[str, Any]]:
        if self.system_prompt:
            return [{"role": "system", "content": self.system_prompt}]
        return []

    def set_tools(self, tools: list[dict]) -> None:
        self.tools = [{"type": "function", "function": tool} for tool in tools]

    async def generate(self, message: str | None = None) -> LLMResponse:

        if message:
            self.messages.append({"role": "user", "content": message})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "temperature": self.temperature,
        }
        if self.tools:
            kwargs["tools"] = self.tools
            kwargs["tool_choice"] = "auto"
        if self.fallback_models:
            # Tính năng của OpenRouter: thử lần lượt các model nếu model đầu lỗi.
            kwargs["extra_body"] = {"models": [self.model, *self.fallback_models]}

        response = await self.client.chat.completions.create(**kwargs)

        # OpenRouter đôi khi trả HTTP 200 nhưng body chứa lỗi và không có choices.
        if not getattr(response, "choices", None):
            err = (response.model_extra or {}).get("error")
            raise RuntimeError(f"LLM không trả về choices: {err or response}")

        assistant = response.choices[0].message

        calls: list[ToolCall] = []
        serialized_calls: list[dict[str, Any]] = []

        for call in assistant.tool_calls or []:
            raw_args = call.function.arguments or "{}"
            try:
                arguments = json.loads(raw_args)
                if not isinstance(arguments, dict):
                    raise ValueError("arguments is not an object")
            except (json.JSONDecodeError, ValueError):
                log.warning("Tool %s trả arguments không hợp lệ: %r", call.function.name, raw_args)
                arguments = {}
                raw_args = "{}"  # gửi lại history bằng JSON hợp lệ để server không từ chối

            serialized_calls.append({
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": raw_args},
            })
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": assistant.content or "",
        }
        if serialized_calls:
            assistant_message["tool_calls"] = serialized_calls
            # Model reasoning (qua OpenRouter) có thể cần nhận lại phần suy luận
            # của lượt trước khi bạn trả kết quả tool.
            reasoning_details = getattr(assistant, "reasoning_details", None)
            if reasoning_details:
                assistant_message["reasoning_details"] = reasoning_details

        # QUAN TRỌNG: phải lưu message của assistant vào history,
        # nếu không message "tool" phía sau sẽ không có tool_calls đi trước.
        self.messages.append(assistant_message)

        self.pending_calls = {c.id: c.name for c in calls}

        return LLMResponse(text=assistant.content or "", tool_calls=calls)

    def _clip(self, text: str) -> str:
        if len(text) <= self.max_tool_chars:
            return text
        dropped = len(text) - self.max_tool_chars
        return (
            text[: self.max_tool_chars]
            + f"\n...[đã cắt {dropped} ký tự; hãy gọi lại với phạm vi hẹp hơn]"
        )

    def add_tool_results(self, results: list[tuple[str, dict]]) -> None:
        expected = set(self.pending_calls)
        received = {call_id for call_id, _ in results}

        if expected != received:
            raise ValueError(
                f"Tool results mismatch: missing={expected - received}, "
                f"unexpected={received - expected}"
            )

        for call_id, result in results:
            content = json.dumps(result, ensure_ascii=False, default=str)
            self.messages.append({
                "role": "tool",
                "name": self.pending_calls[call_id],
                "tool_call_id": call_id,
                "content": self._clip(content),
            })

        self.pending_calls.clear()

    def load_history(self, message) -> None:
        self.pending_calls = {}
        self.messages = [*self._base_messages(), *message]

    async def close(self) -> None:
        await self.client.close()
