import logging
from typing import Any

from pydantic import BaseModel, ValidationError

from src.llm.base import LLMClient, LLMResponse, LLMTruncated, Stage, call_with_tracking

logger = logging.getLogger(__name__)

MAX_PARSE_ATTEMPTS = 2
TRUNCATION_RETRY_FACTOR = 2


class StructuredOutputError(Exception):
    """The model returned output that never validated against the requested schema."""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.removesuffix("```")
    return text.strip()


def _call(
    client: LLMClient, run_id: str, stage: Stage, system: str, prompt: str, max_tokens: int,
    schema: dict[str, Any], episode_no: int | None,
) -> LLMResponse:
    return call_with_tracking(
        client, run_id=run_id, stage=stage, system=system, prompt=prompt, max_tokens=max_tokens,
        response_schema=schema, episode_no=episode_no,
    )


def generate_model[M: BaseModel](
    client: LLMClient,
    model_cls: type[M],
    *,
    run_id: str,
    stage: Stage,
    system: str,
    prompt: str,
    max_tokens: int,
    episode_no: int | None = None,
) -> M:
    """Structured call with one bounded repair attempt: on a validation failure the error is
    fed back once, then StructuredOutputError is raised for the caller to escalate."""
    schema = model_cls.model_json_schema()
    error: str | None = None
    for attempt in range(MAX_PARSE_ATTEMPTS):
        attempt_prompt = prompt
        if error:
            attempt_prompt += f"\n\nYour previous response failed validation:\n{error}\nReturn corrected JSON only."
        try:
            response = _call(client, run_id, stage, system, attempt_prompt, max_tokens, schema, episode_no)
        except LLMTruncated:
            max_tokens *= TRUNCATION_RETRY_FACTOR
            logger.warning("structured output truncated stage=%s; retrying once with max_tokens=%d", stage, max_tokens)
            response = _call(client, run_id, stage, system, attempt_prompt, max_tokens, schema, episode_no)
        try:
            return model_cls.model_validate_json(_strip_code_fence(response.text))
        except ValidationError as exc:
            error = str(exc)[:1500]
            logger.warning("structured output invalid stage=%s attempt=%d model=%s", stage, attempt, model_cls.__name__)
    raise StructuredOutputError(f"{model_cls.__name__} never validated after {MAX_PARSE_ATTEMPTS} attempts: {error}")
