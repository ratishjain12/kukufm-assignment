# 02 — LLM Client Interface (`src/llm/`)

## Goal
One interface, three interchangeable providers (Anthropic, OpenAI, Bedrock). Provider choice
is a config/env value, never an `if provider == ...` scattered across core logic.

## `base.py`
```python
class LLMResponse(BaseModel):
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str

class LLMClient(Protocol):
    def generate(self, *, system: str, prompt: str, max_tokens: int, temperature: float) -> LLMResponse: ...
```

Wrapper function `call_with_tracking(client, run_id, episode_no, stage, **kwargs) -> LLMResponse`
lives in `base.py` too — every call in `core/` goes through this, not `client.generate()`
directly. It:
- times the call (latency_ms)
- retries on transient failure, bounded (max 3 attempts, exponential backoff), logs each
  retry at WARNING
- computes cost_usd from a per-model price table (config, not hardcoded per provider)
- writes an `LLMCallLog` row via `repo.log_llm_call(...)`
- logs a DEBUG line with the full prompt/response, INFO line with stage/tokens/cost/latency

This is the single choke point for the "traceable: tokens, latency, retries" requirement —
nothing downstream needs to know about it.

## Provider adapters
`anthropic_client.py`, `openai_client.py`, `bedrock_client.py` — each implements `generate()`
only. No retry/logging logic inside adapters (that's the wrapper's job — DRY).

## `factory.py`
```python
def get_client(provider: str | None = None) -> LLMClient:
    provider = provider or os.environ["LLM_PROVIDER"]
    ...
```
Reads provider + model name + credentials from env/config. One place to add a fourth provider
later.

## Model assignment (cost-aware)
- Planner (once): strongest/most expensive model — quality matters, cost is a one-time hit.
- Writer: mid-tier model, this is the bulk of spend across 200 episodes.
- Critic + Extractor: cheapest capable model — structured/short-output tasks, called every
  episode, dominates call *count* even if not cost.

Config in one place (`.env` / `config.py`), not hardcoded in `core/`.

## Implementation notes (deviations from original spec)
- **No `temperature`** in `generate()`. The current Anthropic API has no such parameter; the shared
  interface takes `response_schema: dict | None` instead (native JSON-schema output on Anthropic,
  `response_format` on OpenAI, forced single-tool call on Bedrock Converse).
- **Role vs stage:** `get_client(role)` picks a model (`planner|writer|critic|extractor`);
  `call_with_tracking(stage=...)` labels the call (`plan|draft|critic|extract|revise`). `revise` uses
  the writer client.
- Config lives in `src/config.py` (added; not in the original tree): provider, per-role model
  (`<PROVIDER>_<ROLE>_MODEL`, Anthropic defaults only), and `estimate_cost` backed by `pricing.json`.
- Adapters translate provider exceptions into `LLMError(retryable=...)`; retry policy exists only in
  `base.py` and SDK-internal retries are disabled so every retry is visible in logs and `llm_calls`.
- `.env.example` and an empty `pricing.json` are committed.

## Status: [x] implemented (`src/config.py`, `src/llm/*`), wrapper unit-tested with a fake client
(`tests/test_llm_base.py`). Anthropic adapter constructs and resolves; no live call made. OpenAI and
Bedrock adapters import but are untested against real endpoints.

## Bedrock structured output (later addition)
- Converse native structured output (`outputConfig.textFormat.structure.jsonSchema`, schema passed as a JSON string, per the AWS
  structured-outputs documentation), with `BEDROCK_STRUCTURED_MODE=auto|native|tool`. `auto` falls back to a forced tool call when the model
  replies "doesn't support the outputConfig field" (observed for Amazon Nova) and remembers it per client.
- Supported families per AWS: Anthropic, DeepSeek, Google, MiniMax, Mistral, Moonshot, NVIDIA, OpenAI, Qwen. Schemas need
  `additionalProperties: false` everywhere (our `StrictModel` does this); `anyOf` and internal `$ref` are fine; numeric/string constraints and
  recursion are not allowed (we use none).
- `stopReason` handling: `max_tokens`, `content_filtered`, `guardrail_intervened`, `model_context_window_exceeded` raise a non-retryable
  `LLMError` for structured calls; `malformed_model_output` / `malformed_tool_use` are retryable.
- Whitespace-loop guard: `stopReason=max_tokens` with a trailing whitespace run of 100+ characters is treated as degenerate decoding
  (seen with Kimi K2.5 on the plan schema). `auto` retries once as a forced tool call; `native` raises an error that says a bigger
  `max_tokens` will not help.
