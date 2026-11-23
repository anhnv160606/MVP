"""Bounded, provider-independent tool-calling loop."""

import asyncio
import inspect
import logging

from llm.base import BaseLLM
from tools.registry import ToolRegistry

log = logging.getLogger(__name__)


class AgentLimitError(RuntimeError):
    """The LLM exhausted its tool-call budget without completing the task."""


async def emit(callback, event):
    if callback:
        result = callback(event)
        if inspect.isawaitable(result):
            await result


class Agent:
    def __init__(self, llm: BaseLLM, registry: ToolRegistry, max_rounds: int = 24):
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        self.llm, self.registry, self.max_rounds = llm, registry, max_rounds

    async def run(self, message: str, *, on_event=None) -> str:
        self.llm.set_tools(self.registry.schemas())
        for _ in range(self.max_rounds):
            response = await self.llm.generate(message)
            message = None
            if response.text:
                await emit(on_event, {"type": "message", "text": response.text})
            if not response.tool_calls:
                return response.text
            results = []
            ids = [call.id for call in response.tool_calls]
            if len(set(ids)) != len(ids):
                raise ValueError("LLM returned duplicate tool-call IDs")
            for call in response.tool_calls:
                await emit(on_event, {"type": "tool_start", "name": call.name})
                try:
                    operation = asyncio.create_task(
                        asyncio.to_thread(self.registry.execute, call.name, call.arguments)
                    )
                    try:
                        result = await asyncio.shield(operation)
                    except asyncio.CancelledError:
                        # A DB write already running in a worker cannot be cancelled.
                        # Wait for its draft pointer before handing control back.
                        await asyncio.gather(operation, return_exceptions=True)
                        raise
                except (ValueError, TypeError, KeyError) as error:
                    result = {"error": {"code": "INVALID_TOOL_CALL", "message": str(error)}}
                except Exception:
                    log.exception("Tool failed: %s", call.name)
                    result = {
                        "error": {
                            "code": "TOOL_FAILED",
                            "message": "Backend operation failed; inspect server logs.",
                        }
                    }
                results.append((call.id, result))
                await emit(on_event, {"type": "tool_result", "name": call.name, "result": result})
            self.llm.add_tool_results(results)
        raise AgentLimitError("Agent tool-call budget exhausted; saved drafts remain available")
