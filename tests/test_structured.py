import pytest
from pydantic import BaseModel

from src.llm.structured import StructuredOutputError, generate_model
from src.prompts import render
from tests.fakes import ScriptedClient


class Out(BaseModel):
    n: int


def _gen(client, run_id):
    return generate_model(client, Out, run_id=run_id, stage="extract", system="s", prompt="p", max_tokens=50)


def test_valid_json_parses(run_id):
    assert _gen(ScriptedClient(['{"n": 3}']), run_id).n == 3


def test_code_fenced_json_is_accepted(run_id):
    assert _gen(ScriptedClient(['```json\n{"n": 4}\n```']), run_id).n == 4


def test_invalid_then_valid_repairs_once_and_feeds_error_back(run_id):
    client = ScriptedClient(["not json", '{"n": 5}'])
    assert _gen(client, run_id).n == 5
    assert "failed validation" in client.calls[1][1]


def test_never_valid_raises_after_bounded_attempts(run_id):
    client = ScriptedClient(["nope", "still nope", '{"n": 1}'])
    with pytest.raises(StructuredOutputError):
        _gen(client, run_id)
    assert len(client.calls) == 2


def test_render_splits_system_and_user_and_fills_vars(tmp_path, monkeypatch):
    from src import prompts

    (tmp_path / "demo.md").write_text("sys $who\n=== USER ===\nhello $who, cost $$5")
    monkeypatch.setattr(prompts, "PROMPT_DIR", tmp_path)
    prompt = render("demo", who="bob")
    assert prompt.system == "sys bob"
    assert prompt.user == "hello bob, cost $5"


class _TruncatingClient:
    provider = "fake"
    model = "fake-model"

    def __init__(self, truncations: int):
        self.truncations = truncations
        self.max_tokens_seen: list[int] = []

    def generate(self, *, system, prompt, max_tokens, response_schema=None):
        from src.llm.base import LLMResponse, LLMTruncated

        self.max_tokens_seen.append(max_tokens)
        if len(self.max_tokens_seen) <= self.truncations:
            raise LLMTruncated("cut off")
        return LLMResponse(text='{"n": 9}', prompt_tokens=1, completion_tokens=1, model=self.model)


def test_a_truncated_response_is_retried_once_with_double_the_tokens(run_id):
    client = _TruncatingClient(truncations=1)
    assert _gen(client, run_id).n == 9
    assert client.max_tokens_seen == [50, 100]


def test_truncation_that_persists_after_the_larger_cap_raises(run_id):
    from src.llm.base import LLMTruncated

    client = _TruncatingClient(truncations=2)
    with pytest.raises(LLMTruncated):
        _gen(client, run_id)
    assert client.max_tokens_seen == [50, 100]
