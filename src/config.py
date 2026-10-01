"""Provider, per-role model, and pricing configuration. Everything environment-specific
lives here so core/ never hardcodes a model name or a price."""

import json
import logging
import os
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

Role = Literal["planner", "writer", "critic", "extractor"]
StructuredMode = Literal["auto", "native", "tool"]

# Stopping rules. Cost caps only bite once pricing.json has real rates (unknown models cost 0.0).
MAX_REVISIONS = int(os.environ.get("MAX_REVISIONS", "2"))
MAX_EPISODE_COST_USD = float(os.environ.get("MAX_EPISODE_COST_USD", "0.50"))
MAX_RUN_COST_USD = float(os.environ.get("MAX_RUN_COST_USD", "0"))  # 0 disables the run budget
# Per-request ceiling in seconds. A normal 200-episode plan call takes under a minute; longer usually means a stuck generation.
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))

# Bedrock structured output: "native" (schema-constrained decoding), "tool" (forced tool call), or "auto"
# (native, falling back to a tool call for models that reject it, such as Amazon Nova).
_structured_mode = os.environ.get("BEDROCK_STRUCTURED_MODE", "auto").lower()
if _structured_mode not in ("auto", "native", "tool"):
    raise ValueError(f"BEDROCK_STRUCTURED_MODE must be auto, native or tool, got {_structured_mode!r}")
BEDROCK_STRUCTURED_MODE = cast(StructuredMode, _structured_mode)

PRICING_FILE = Path(os.environ.get("LLM_PRICING_FILE", Path(__file__).resolve().parent.parent / "pricing.json"))

# Only Anthropic defaults are filled in; OpenAI/Bedrock model ids must come from .env so we
# never run against a guessed identifier.
DEFAULT_MODELS: dict[str, dict[Role, str]] = {
    "anthropic": {
        "planner": "claude-opus-5-5",
        "writer": "claude-sonnet-5-5",
        "critic": "claude-haiku-4-5-20251001",
        "extractor": "claude-haiku-4-5-20251001",
    },
}


def get_provider() -> str:
    return os.environ.get("LLM_PROVIDER", "anthropic").lower()


def get_model_for_role(provider: str, role: Role) -> str:
    env_key = f"{provider.upper()}_{role.upper()}_MODEL"
    model = os.environ.get(env_key) or DEFAULT_MODELS.get(provider, {}).get(role)
    if not model:
        raise ValueError(f"No model configured for provider={provider!r} role={role!r}. Set {env_key} in .env.")
    return model


def _load_pricing() -> dict[str, dict[str, float]]:
    if not PRICING_FILE.exists():
        return {}
    return json.loads(PRICING_FILE.read_text())


_warned_models: set[str] = set()


def estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD cost from pricing.json (per-million-token rates keyed "provider/model").

    Unknown models cost 0.0 and warn once — cost numbers are only as good as pricing.json.
    """
    key = f"{provider}/{model}"
    entry = _load_pricing().get(key)
    if entry is None:
        if key not in _warned_models:
            _warned_models.add(key)
            logger.warning("no pricing entry for %s in %s; cost recorded as 0.0", key, PRICING_FILE)
        return 0.0
    return (
        prompt_tokens / 1_000_000 * entry["input_per_mtok"]
        + completion_tokens / 1_000_000 * entry["output_per_mtok"]
    )
