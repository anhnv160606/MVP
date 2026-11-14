"""Provider cho Gemini API (SDK: google-genai), cùng giao diện với OpenAIProvider.

    pip install google-genai

self.messages vẫn ở dạng OpenAI (system / user / assistant / tool) để
load_history dùng chung. Trước mỗi lần gọi API, _to_contents() chuyển
chúng sang định dạng của Gemini.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from google import genai
from google.genai import types

from .base import BaseLLM, LLMResponse, ToolCall

# Gemini dùng một tập con của OpenAPI schema; các khóa lạ (additionalProperties, $schema,
# title, default...) làm API từ chối request nên phải lọc bỏ.
_ALLOWED_SCHEMA_KEYS = {
    "type", "description", "properties", "required", "items", "enum", "format",
    "nullable", "minimum", "maximum", "minItems", "maxItems", "anyOf",
}


def _sanitize_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_sanitize_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _sanitize_schema(sub) for name, sub in value.items()}
        elif key in ("items", "anyOf"):
            out[key] = _sanitize_schema(value)
        elif key in _ALLOWED_SCHEMA_KEYS:
            out[key] = value

    # JSON Schema cho phép "type": ["string", "null"]; Gemini cần 1 kiểu + nullable.
    t = out.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        out["type"] = non_null[0] if non_null else "string"
        if "null" in t:
            out["nullable"] = True
    return out


def _declaration(tool: dict[str, Any]) -> types.FunctionDeclaration:
    params = tool.get("parameters") or {}
    kwargs: dict[str, Any] = {
        "name": tool["name"],
        "description": tool.get("description") or tool["name"],
    }
    if params.get("properties"):  # tool không có tham số thì bỏ hẳn parameters
        kwargs["parameters"] = _sanitize_schema(params)
    return types.FunctionDeclaration(**kwargs)


class GeminiProvider(BaseLLM):
    def __init__(
        self,
        api_key: str,
        model: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tool_chars: int = 6000,
    ):
        # temperature=None: dùng mặc định của model (Google khuyến nghị giữ mặc định
        # với các model Gemini mới, hạ thấp có thể làm model lặp hoặc suy luận kém).
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.max_tool_chars = max_tool_chars
        self.system_prompt = system_prompt

        self.tools: list[types.Tool] = []
        self.messages: list[dict[str, Any]] = self._base_messages()
        self.pending_calls: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Lịch sử và tool
    # ------------------------------------------------------------------ #

    def _base_messages(self) -> list[dict[str, Any]]:
        if self.system_prompt:
            return [{"role": "system", "content": self.system_prompt}]
        return []

    def set_tools(self, tools: list[dict]) -> None:
        declarations = [_declaration(t) for t in tools]
        self.tools = [types.Tool(function_declarations=declarations)] if declarations else []

    @staticmethod
    def _append_content(contents: list[types.Content], content: types.Content) -> None:
        # Gộp các lượt "user" liền nhau (ví dụ function_response rồi câu hỏi mới)
        # để không gửi hai lượt user liên tiếp.
        if contents and contents[-1].role == "user" and content.role == "user":
            merged = [*(contents[-1].parts or []), *(content.parts or [])]
            contents[-1] = types.Content(role="user", parts=merged)
        else:
            contents.append(content)

    @staticmethod
    def _function_response_part(m: dict[str, Any]) -> types.Part:
        raw = m.get("content") or ""
        try:
            data = json.loads(raw)
            payload = data if isinstance(data, dict) else {"result": data}
        except json.JSONDecodeError:  # kết quả đã bị cắt nên không còn là JSON hợp lệ
            payload = {"result": raw}
        return types.Part.from_function_response(name=m.get("name") or "", response=payload)

    def _to_contents(self) -> tuple[str | None, list[types.Content]]:
        system: str | None = None
        contents: list[types.Content] = []
        tool_parts: list[types.Part] = []

        def flush_tools() -> None:
            nonlocal tool_parts
            if tool_parts:
                self._append_content(contents, types.Content(role="user", parts=tool_parts))
                tool_parts = []

        for m in self.messages:
            role = m["role"]

            if role == "system":
                system = m["content"]
                continue

            if role == "tool":
                # Mọi kết quả tool của cùng một lượt phải nằm trong MỘT message.
                tool_parts.append(self._function_response_part(m))
                continue

            flush_tools()

            if role == "user":
                text = m.get("content") or ""
                self._append_content(
                    contents, types.Content(role="user", parts=[types.Part(text=text)])
                )
            elif role == "assistant":
                raw = m.get("_raw")
                if raw is not None:
                    # Dùng lại nguyên message gốc của model: giữ thought signature,
                    # cần thiết cho function calling ở các model Gemini 3.
                    contents.append(raw)
                    continue
                parts: list[types.Part] = []
                if m.get("content"):
                    parts.append(types.Part(text=m["content"]))
                for tc in m.get("tool_calls") or []:
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    parts.append(types.Part(function_call=types.FunctionCall(
                        name=tc["function"]["name"], args=args,
                    )))
                if parts:  # bỏ qua assistant rỗng
                    contents.append(types.Content(role="model", parts=parts))

        flush_tools()
        return system, contents

    # ------------------------------------------------------------------ #
    # Gọi model
    # ------------------------------------------------------------------ #

    async def generate(self, message: str | None = None) -> LLMResponse:
        if message:
            self.messages.append({"role": "user", "content": message})

        system, contents = self._to_contents()
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=self.tools or None,
            temperature=self.temperature,
        )

        response = await self.client.aio.models.generate_content(
            model=self.model, contents=contents, config=config,
        )

        candidates = getattr(response, "candidates", None)
        if not candidates:
            feedback = getattr(response, "prompt_feedback", None)
            raise RuntimeError(f"Gemini không trả về candidate: {feedback or response}")

        content = candidates[0].content
        parts = list(content.parts or []) if content else []

        text = "".join(
            p.text for p in parts if p.text and not getattr(p, "thought", False)
        )

        calls: list[ToolCall] = []
        serialized_calls: list[dict[str, Any]] = []
        for p in parts:
            fc = p.function_call
            if not fc:
                continue
            call_id = fc.id or f"call_{uuid.uuid4().hex[:10]}"  # Gemini thường không trả id
            arguments = dict(fc.args or {})
            serialized_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": fc.name,
                    "arguments": json.dumps(arguments, ensure_ascii=False, default=str),
                },
            })
            calls.append(ToolCall(id=call_id, name=fc.name, arguments=arguments))

        assistant_message: dict[str, Any] = {"role": "assistant", "content": text}
        if serialized_calls:
            assistant_message["tool_calls"] = serialized_calls
        if parts:
            assistant_message["_raw"] = content  # để gửi lại nguyên vẹn ở lượt sau

        # Phải lưu message của assistant, nếu không kết quả tool phía sau sẽ mồ côi.
        self.messages.append(assistant_message)
        self.pending_calls = {c.id: c.name for c in calls}

        return LLMResponse(text=text, tool_calls=calls)

    # ------------------------------------------------------------------ #
    # Kết quả tool, lịch sử, đóng
    # ------------------------------------------------------------------ #

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
        aclose = getattr(getattr(self.client, "aio", None), "aclose", None)
        if aclose:
            await aclose()
