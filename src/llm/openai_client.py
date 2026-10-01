import os
from typing import Any

import openai

from src.config import LLM_TIMEOUT_SECONDS
from src.llm.base import RETRYABLE_STATUS_CODES, LLMError, LLMResponse


class OpenAIClient:
    provider = "openai"

    def __init__(self, model: str):
        self.model = model
        self._client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=LLM_TIMEOUT_SECONDS)

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {}
        if response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": response_schema, "strict": False},
            }

        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                max_completion_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                **kwargs,
            )
        except openai.APIConnectionError as exc:
            raise LLMError(str(exc), retryable=True) from exc
        except openai.APIStatusError as exc:
            raise LLMError(str(exc), retryable=exc.status_code in RETRYABLE_STATUS_CODES) from exc

        return LLMResponse(
            text=completion.choices[0].message.content or "",
            prompt_tokens=completion.usage.prompt_tokens,
            completion_tokens=completion.usage.completion_tokens,
            model=self.model,
        )
