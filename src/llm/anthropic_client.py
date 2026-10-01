import os
from typing import Any

import anthropic

from src.config import LLM_TIMEOUT_SECONDS
from src.llm.base import RETRYABLE_STATUS_CODES, LLMError, LLMResponse


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, model: str):
        self.model = model
        # SDK-internal retries disabled: call_with_tracking owns retry policy so every retry is logged.
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0, timeout=LLM_TIMEOUT_SECONDS)

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
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(response_schema)}
            }

        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
        except anthropic.APIConnectionError as exc:
            raise LLMError(str(exc), retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(str(exc), retryable=exc.status_code in RETRYABLE_STATUS_CODES) from exc

        text = "".join(block.text for block in message.content if block.type == "text")
        return LLMResponse(
            text=text,
            prompt_tokens=message.usage.input_tokens,
            completion_tokens=message.usage.output_tokens,
            model=self.model,
        )
