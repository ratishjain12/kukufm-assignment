import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from src.llm.base import LLMResponse

Handler = Callable[[str, str, dict[str, Any] | None], str]


class ScriptedClient:
    """Fake LLMClient: replies from a queue of canned strings, or from a handler(system, prompt, schema)."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, replies: list[str | BaseModel] | Handler):
        self._replies = replies
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def generate(self, *, system: str, prompt: str, max_tokens: int, response_schema: dict[str, Any] | None = None) -> LLMResponse:
        self.calls.append((system, prompt, response_schema))
        if callable(self._replies):
            text = self._replies(system, prompt, response_schema)
        else:
            reply = self._replies.pop(0)
            text = reply.model_dump_json() if isinstance(reply, BaseModel) else reply
        return LLMResponse(text=text, prompt_tokens=100, completion_tokens=50, model=self.model)


def as_json(obj: BaseModel | dict) -> str:
    return obj.model_dump_json() if isinstance(obj, BaseModel) else json.dumps(obj)
