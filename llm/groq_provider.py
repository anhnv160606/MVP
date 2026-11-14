import json
from groq import AsyncGroq 
from .base import BaseLLM, LLMResponse, ToolCall

class GroqProvider(BaseLLM):
    def __init__(self, api_key: str, model: str, system_prompt: str | None = None):
        self.client = AsyncGroq(api_key=api_key)
        self.model = model
        self.tools = []
        self.messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
        self.pending_calls = {}
        self.system_prompt = system_prompt


    def set_tools(self, tools: list[dict]) -> None:
        self.tools = [{"type": "function", "function": tool} for tool in tools]

    async def generate(self, message: str | None = None) -> LLMResponse:

        if message:
            self.messages.append({"role": "user", "content": message})

        kwargs = {
            "model": self.model,
            "messages": self.messages,
            "temperature": 0.2,
        }

        if self.tools:
            kwargs["tools"] = self.tools 
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(
            **kwargs
        )

        assistant = response.choices[0].message

        calls = []
        serialized_calls = []

        for call in assistant.tool_calls or []:
            serialized_calls.append({
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            })

            try:
                arguments = json.loads(
                    call.function.arguments
                )
                if not isinstance(arguments, dict):
                    arguments = {}
            except json.JSONDecodeError:
                arguments = {}

            calls.append(ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=arguments,
            ))

        assistant_message = {
            "role": "assistant",
            "content": assistant.content,
        }

        if serialized_calls:
            assistant_message["tool_calls"] = serialized_calls
        
        self.pending_calls = {
            call.id: call.name for call in calls
        }

        return LLMResponse(text = assistant.content or "",
                           tool_calls = calls)

    def add_tool_results(self, results: list[tuple[str, dict]]) -> None:

        expected = set(self.pending_calls)
        received = {call_id for call_id, _ in results}

        if expected != received:
            missing_tools = expected - received
            raise ValueError('Incomplete tool: {missing_tools}')
            

        for call_id, result in results:
            self.messages.append({
                "role": "tool",
                "name": self.pending_calls[call_id],
                "tool_call_id": call_id,
                "content": json.dumps(
                    result,
                    ensure_ascii=False,
                    default=str,
                ),
            })

        self.pending_calls.clear()

    async def close(self) -> None:
        await self.client.close()

    def load_history(self, message):
        self.pending_calls = {}

        self.messages = [
            {
                "role": "system",
                "content": self.system_prompt,
            },
            *message,
        ]

    




            









