from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, Field 

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]

class LLMResponse(BaseModel):
    text: str = ""
    tool_calls : list[ToolCall] = Field(default_factory=list)

class BaseLLM(ABC):
    @abstractmethod
    async def generate(self, message: str | None = None) -> LLMResponse:
        """Generate a response from the LLM given a prompt."""
        pass

    @abstractmethod
    def add_tool_results(self, tool_results: list[tuple[str, dict]]) -> None:
        """Add the results of tool calls to the LLM response."""
        ...

    @abstractmethod
    def set_tools(self, tools: list[dict]) -> None:
        """Set the tools available to the LLM."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the LLM and release any resources."""
        ...

    @abstractmethod
    def load_history(self, messages: list[dict[str, str]]) -> None:
        ...



    


