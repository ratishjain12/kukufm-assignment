"""LLM client contract plus the single choke point (call_with_tracking) every call in core/
goes through: bounded retries, latency/cost logging, and persistence to llm_calls."""

import logging
import time
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from src.config import estimate_cost
from src.models import LLMCallLog
from src.storage import repo

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2.0
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504, 529})

Stage = Literal["plan", "draft", "critic", "extract", "revise", "summarize"]


class LLMResponse(BaseModel):
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class LLMTruncated(LLMError):
    """The response hit max_tokens, so a structured answer is cut off. Not retryable as is: callers may retry with a larger cap."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)


class LLMClient(Protocol):
    provider: str
    model: str

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Free-form text when response_schema is None; otherwise `text` is a JSON string
        conforming to the given JSON schema."""
        ...


def call_with_tracking(
    client: LLMClient,
    *,
    run_id: str,
    stage: Stage,
    system: str,
    prompt: str,
    max_tokens: int,
    response_schema: dict[str, Any] | None = None,
    episode_no: int | None = None,
) -> LLMResponse:
    for attempt in range(MAX_ATTEMPTS):
        started = time.monotonic()
        try:
            response = client.generate(
                system=system, prompt=prompt, max_tokens=max_tokens, response_schema=response_schema
            )
        except LLMError as exc:
            final_attempt = attempt == MAX_ATTEMPTS - 1
            if not exc.retryable or final_attempt:
                level = logging.WARNING if isinstance(exc, LLMTruncated) else logging.ERROR  # callers retry truncation with a larger cap
                logger.log(level, "llm call failed stage=%s attempt=%d retryable=%s error=%s", stage, attempt, exc.retryable, exc)
                raise
            wait = BACKOFF_BASE_SECONDS * 2**attempt
            logger.warning("llm call retry stage=%s attempt=%d wait=%.1fs error=%s", stage, attempt, wait, exc)
            time.sleep(wait)
            continue

        latency_ms = int((time.monotonic() - started) * 1000)
        cost = estimate_cost(client.provider, response.model, response.prompt_tokens, response.completion_tokens)
        logger.info(
            "llm call ok stage=%s episode=%s model=%s prompt_tokens=%d completion_tokens=%d cost_usd=%.5f latency_ms=%d retries=%d",
            stage, episode_no, response.model, response.prompt_tokens, response.completion_tokens, cost, latency_ms, attempt,
        )
        logger.debug("llm call detail stage=%s system=%r prompt=%r response=%r", stage, system, prompt, response.text)
        repo.log_llm_call(
            run_id,
            LLMCallLog(
                run_id=run_id,
                episode_no=episode_no,
                stage=stage,
                provider=client.provider,
                model=response.model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
                retry_count=attempt,
            ),
        )
        return response

    raise AssertionError("unreachable: loop either returns or raises")
