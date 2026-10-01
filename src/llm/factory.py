from typing import NamedTuple

from src.config import Role, get_model_for_role, get_provider
from src.llm.base import LLMClient

SUPPORTED_PROVIDERS = ("anthropic", "openai", "bedrock")


class ClientSet(NamedTuple):
    planner: LLMClient
    writer: LLMClient
    critic: LLMClient
    extractor: LLMClient


def get_client(role: Role, provider: str | None = None) -> LLMClient:
    provider = (provider or get_provider()).lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unknown LLM provider: {provider!r} (expected one of {SUPPORTED_PROVIDERS})")
    model = get_model_for_role(provider, role)

    if provider == "anthropic":
        from src.llm.anthropic_client import AnthropicClient

        return AnthropicClient(model)
    if provider == "openai":
        from src.llm.openai_client import OpenAIClient

        return OpenAIClient(model)

    from src.llm.bedrock_client import BedrockClient

    return BedrockClient(model)


def build_clients(provider: str | None = None) -> ClientSet:
    return ClientSet(*(get_client(role, provider) for role in ("planner", "writer", "critic", "extractor")))
