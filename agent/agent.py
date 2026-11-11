"""Provider-neutral tool-calling loop for the electrical design Agent.

The provider callback receives OpenAI-compatible ``messages`` and ``tools``
and returns an assistant message (or a Chat Completions response). The caller
owns credentials, prompts, conversation persistence and WebSocket streaming.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _assistant_message(response: Any) -> dict[str, Any]:
    """Normalize a raw message or an OpenAI-compatible completion response."""
    choices = _value(response, "choices")
    message = _value(choices[0], "message") if choices else response
    role = _value(message, "role", "assistant")
    content = _value(message, "content")
    raw_calls = _value(message, "tool_calls", []) or []
    calls = []
    for call in raw_calls:
        fn = _value(call, "function", {})
        calls.append({"id": _value(call, "id"), "type": "function",
                      "function": {"name": _value(fn, "name"), "arguments": _value(fn, "arguments", "{}")}})
    result = {"role": role, "content": content}
    if calls:
        result["tool_calls"] = calls
    return result


class ToolCallingAgent:
    """Run one model turn and execute its Phase-scoped tool calls."""

    def __init__(self, complete: Callable[..., Any], toolset: dict[str, Any], *, max_tool_rounds: int = 8):
        if not callable(complete):
            raise TypeError("complete must be a callable model-provider adapter")
        if not isinstance(toolset.get("tools"), list) or not callable(toolset.get("execute")):
            raise TypeError("toolset must contain a tools list and execute(name, arguments) callable")
        if max_tool_rounds < 1:
            raise ValueError("max_tool_rounds must be at least one")
        self.complete = complete
        self.toolset = toolset
        self.max_tool_rounds = max_tool_rounds
        self._tool_names = {item["function"]["name"] for item in toolset["tools"]}

    async def _complete(self, messages: list[dict[str, Any]]) -> Any:
        result = self.complete(messages=messages, tools=self.toolset["tools"])
        return await result if inspect.isawaitable(result) else result

    async def _execute(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self.toolset["execute"](name, arguments)
        return await result if inspect.isawaitable(result) else result

    async def run(self, user_message: str, *, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not isinstance(user_message, str) or not user_message.strip():
            raise ValueError("user_message must be a non-empty string")
        history = list(messages or [])
        history.append({"role": "user", "content": user_message})
        for _ in range(self.max_tool_rounds):
            assistant = _assistant_message(await self._complete(history))
            calls = assistant.get("tool_calls", [])
            history.append(assistant)
            if not calls:
                return {"content": assistant.get("content"), "messages": history}
            for call in calls:
                function = call["function"]
                name = function.get("name")
                call_id = call.get("id") or "missing_tool_call_id"
                if name not in self._tool_names:
                    output: Any = {"error": "unknown_tool", "tool_name": name}
                else:
                    try:
                        raw_arguments = function.get("arguments") or "{}"
                        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                        if not isinstance(arguments, dict):
                            raise ValueError("Tool arguments must be a JSON object.")
                        output = await self._execute(name, arguments)
                    except Exception as exc:
                        output = {"error": type(exc).__name__, "message": str(exc)}
                history.append({"role": "tool", "tool_call_id": call_id,
                                "content": json.dumps(output, ensure_ascii=False, default=str)})
        return {"content": None, "error": "tool_call_limit_reached", "messages": history}
