import json

import pytest

from src.llm import base
from src.llm.base import LLMError, LLMResponse, call_with_tracking
from src.storage import repo


class FakeClient:
    provider = "fake"
    model = "fake-model"

    def __init__(self, outcomes: list[LLMError | LLMResponse]):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate(self, *, system, prompt, max_tokens, response_schema=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, LLMError):
            raise outcome
        return outcome


OK = LLMResponse(text="hi", prompt_tokens=10, completion_tokens=20, model="fake-model")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda _: None)


def _call(client, run_id):
    return call_with_tracking(client, run_id=run_id, stage="draft", system="s", prompt="p", max_tokens=100, episode_no=1)


def test_success_logs_one_row(run_id):
    client = FakeClient([OK])
    assert _call(client, run_id).text == "hi"
    summary = repo.sum_run_cost(run_id)
    assert summary["calls"] == 1
    assert summary["prompt_tokens"] == 10
    assert summary["total_retries"] == 0


def test_retries_then_succeeds_and_records_retry_count(run_id):
    client = FakeClient([LLMError("rate limited", retryable=True), OK])
    _call(client, run_id)
    assert client.calls == 2
    assert repo.sum_run_cost(run_id)["total_retries"] == 1


def test_non_retryable_fails_immediately(run_id):
    client = FakeClient([LLMError("bad key", retryable=False), OK])
    with pytest.raises(LLMError):
        _call(client, run_id)
    assert client.calls == 1
    assert repo.sum_run_cost(run_id)["calls"] == 0


def test_retries_exhausted_raises(run_id):
    client = FakeClient([LLMError("down", retryable=True)] * base.MAX_ATTEMPTS)
    with pytest.raises(LLMError):
        _call(client, run_id)
    assert client.calls == base.MAX_ATTEMPTS


def test_cost_uses_pricing_file(run_id, tmp_path, monkeypatch):
    from src import config

    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({"fake/fake-model": {"input_per_mtok": 1000.0, "output_per_mtok": 2000.0}}))
    monkeypatch.setattr(config, "PRICING_FILE", pricing)
    _call(FakeClient([OK]), run_id)
    assert repo.sum_run_cost(run_id)["total_cost"] == pytest.approx(10 / 1e6 * 1000 + 20 / 1e6 * 2000)
