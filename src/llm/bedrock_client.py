import json
import logging
import os
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ReadTimeoutError

from src.config import BEDROCK_STRUCTURED_MODE, LLM_TIMEOUT_SECONDS, StructuredMode
from src.llm.base import LLMError, LLMResponse, LLMTruncated

logger = logging.getLogger(__name__)

RETRYABLE_ERROR_CODES = frozenset(
    {"ThrottlingException", "ModelTimeoutException", "ServiceUnavailableException", "InternalServerException"}
)
STRUCTURED_OUTPUT_TOOL = "respond"

# stopReason values after which a structured response cannot be trusted. Truncation is not retryable: a repair
# attempt would hit the same cap. A malformed generation is a model glitch worth one more try.
UNUSABLE_STOP_REASONS = {
    "max_tokens": False,
    "model_context_window_exceeded": False,
    "content_filtered": False,
    "guardrail_intervened": False,
    "malformed_model_output": True,
    "malformed_tool_use": True,
}


WHITESPACE_LOOP_CHARS = 100


def _whitespace_loop(text: str) -> bool:
    """Schema-constrained decoding can degenerate into endless newlines/tabs (observed with Kimi K2.5 on the plan
    schema), burning the whole token budget on blank space."""
    return len(text) - len(text.rstrip()) >= WHITESPACE_LOOP_CHARS


def _native_unsupported(error: ClientError) -> bool:
    """True when Bedrock rejects native structured output because the model lacks it. Observed for Amazon Nova:
    "This model doesn't support the outputConfig field. Remove outputConfig and try again." """
    details = error.response.get("Error", {})
    message = details.get("Message", "").lower()
    return details.get("Code") == "ValidationException" and "outputconfig" in message and "support" in message


class BedrockClient:
    """Converse API client. Structured output uses Bedrock's native schema-constrained decoding where the model
    supports it (auto mode falls back to a forced tool call, e.g. for Amazon Nova, and remembers the result)."""

    provider = "bedrock"

    def __init__(self, model: str, structured_mode: StructuredMode = BEDROCK_STRUCTURED_MODE):
        self.model = model
        self._mode = structured_mode
        self._native_supported: bool | None = None if structured_mode == "auto" else structured_mode == "native"
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
            config=Config(retries={"max_attempts": 1, "mode": "standard"}, read_timeout=LLM_TIMEOUT_SECONDS),
        )

    @staticmethod
    def _native_config(schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "schema": json.dumps(schema),
                            "name": str(schema.get("title", "response")),
                            "description": "Structured response",
                        }
                    },
                }
            }
        }

    @staticmethod
    def _tool_config(schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": STRUCTURED_OUTPUT_TOOL,
                            "description": "Return the final structured response.",
                            "inputSchema": {"json": schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": STRUCTURED_OUTPUT_TOOL}},
            }
        }

    def _converse(self, system: str, prompt: str, max_tokens: int, extra: dict[str, Any]) -> dict[str, Any]:
        return self._client.converse(
            modelId=self.model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens},
            **extra,
        )

    @staticmethod
    def _text_of(response: dict[str, Any]) -> str:
        return "".join(b.get("text", "") for b in response["output"]["message"]["content"])

    def _degenerate(self, response: dict[str, Any]) -> bool:
        return response.get("stopReason") == "max_tokens" and _whitespace_loop(self._text_of(response))

    def _call(self, system: str, prompt: str, max_tokens: int, schema: dict[str, Any] | None) -> tuple[dict[str, Any], int, int]:
        """Returns the usable response plus the (input, output) tokens of any discarded attempt, which are still billed."""
        if schema is None:
            return self._converse(system, prompt, max_tokens, {}), 0, 0
        if self._native_supported is False:
            return self._converse(system, prompt, max_tokens, self._tool_config(schema)), 0, 0
        try:
            response = self._converse(system, prompt, max_tokens, self._native_config(schema))
        except ClientError as exc:
            if self._mode != "auto" or not _native_unsupported(exc):
                raise
            logger.warning("model %s does not support native structured output; using forced tool call instead", self.model)
            self._native_supported = False
            return self._converse(system, prompt, max_tokens, self._tool_config(schema)), 0, 0
        except ReadTimeoutError:
            if self._mode != "auto":
                raise
            logger.warning("model %s timed out on native structured output (likely a whitespace loop); retrying once as a forced tool call", self.model)
            return self._converse(system, prompt, max_tokens, self._tool_config(schema)), 0, 0
        self._native_supported = True
        if self._mode == "auto" and self._degenerate(response):
            wasted_in, wasted_out = response["usage"]["inputTokens"], response["usage"]["outputTokens"]
            logger.warning(
                "model %s fell into a whitespace loop under native decoding (%d output tokens wasted); retrying once as a forced tool call",
                self.model, wasted_out,
            )
            return self._converse(system, prompt, max_tokens, self._tool_config(schema)), wasted_in, wasted_out
        return response, 0, 0

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        try:
            response, wasted_in, wasted_out = self._call(system, prompt, max_tokens, response_schema)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            raise LLMError(str(exc), retryable=code in RETRYABLE_ERROR_CODES) from exc
        except BotoCoreError as exc:
            raise LLMError(str(exc), retryable=True) from exc

        stop_reason = response.get("stopReason", "")
        blocks = response["output"]["message"]["content"]
        tool_input = next((b["toolUse"]["input"] for b in blocks if "toolUse" in b), None)
        text = json.dumps(tool_input) if tool_input is not None else self._text_of(response)

        if response_schema is not None and stop_reason in UNUSABLE_STOP_REASONS:
            if self._degenerate(response):
                advice = "the model fell into a whitespace loop, so a larger max_tokens will not help; try BEDROCK_STRUCTURED_MODE=tool or another model"
            elif stop_reason == "max_tokens":
                advice = "raise max_tokens for this call"
            else:
                advice = "see the Bedrock docs for this stop reason"
            message = f"structured response from {self.model} is unusable (stopReason={stop_reason}); {advice}"
            if stop_reason == "max_tokens" and not self._degenerate(response):
                raise LLMTruncated(message)
            raise LLMError(message, retryable=UNUSABLE_STOP_REASONS[stop_reason])
        if response_schema is None and stop_reason == "max_tokens":
            logger.warning("plain response from %s hit max_tokens and may be cut off", self.model)

        return LLMResponse(
            text=text,
            prompt_tokens=response["usage"]["inputTokens"] + wasted_in,
            completion_tokens=response["usage"]["outputTokens"] + wasted_out,
            model=self.model,
        )
