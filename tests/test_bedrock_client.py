import json

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from src.llm import bedrock_client
from src.llm.base import LLMError, LLMTruncated
from src.llm.bedrock_client import BedrockClient

SCHEMA = {"title": "Verdict", "type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"], "additionalProperties": False}

NOVA_ERROR = "This model doesn't support the outputConfig field. Remove outputConfig and try again."


def client_error(code: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


def reply(*, text: str | None = None, tool_input: dict | None = None, stop: str = "end_turn", extra_blocks: list | None = None) -> dict:
    blocks: list = list(extra_blocks or [])
    if text is not None:
        blocks.append({"text": text})
    if tool_input is not None:
        blocks.append({"toolUse": {"toolUseId": "t1", "name": "respond", "input": tool_input}})
    return {"output": {"message": {"role": "assistant", "content": blocks}}, "stopReason": stop, "usage": {"inputTokens": 11, "outputTokens": 7}}


class FakeBoto:
    def __init__(self, outcomes: list[dict | Exception]):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make(monkeypatch, outcomes, mode="auto") -> tuple[BedrockClient, FakeBoto]:
    fake = FakeBoto(outcomes)
    monkeypatch.setattr(bedrock_client.boto3, "client", lambda *a, **k: fake)
    return BedrockClient("some.model", structured_mode=mode), fake


def generate(client: BedrockClient, schema=SCHEMA):
    return client.generate(system="sys", prompt="p", max_tokens=100, response_schema=schema)


def test_plain_call_sends_no_structured_config_and_ignores_reasoning_blocks(monkeypatch):
    client, fake = make(monkeypatch, [reply(text="Hello.", extra_blocks=[{"reasoningContent": {"reasoningText": {"text": "thinking"}}}])])
    result = client.generate(system="sys", prompt="p", max_tokens=100)
    assert result.text == "Hello." and (result.prompt_tokens, result.completion_tokens) == (11, 7)
    assert "outputConfig" not in fake.calls[0] and "toolConfig" not in fake.calls[0]
    assert fake.calls[0]["inferenceConfig"] == {"maxTokens": 100}


def test_native_request_matches_documented_shape(monkeypatch):
    client, fake = make(monkeypatch, [reply(text='{"n": 1}')])
    assert generate(client).text == '{"n": 1}'
    config = fake.calls[0]["outputConfig"]["textFormat"]
    assert config["type"] == "json_schema"
    spec = config["structure"]["jsonSchema"]
    assert json.loads(spec["schema"]) == SCHEMA and isinstance(spec["schema"], str)
    assert spec["name"] == "Verdict"
    assert "toolConfig" not in fake.calls[0]


def test_native_response_with_reasoning_block_first_still_parses(monkeypatch):
    client, _ = make(monkeypatch, [reply(text='{"n": 2}', extra_blocks=[{"reasoningContent": {"reasoningText": {"text": "hmm"}}}])])
    assert generate(client).text == '{"n": 2}'


@pytest.mark.parametrize("stop, retryable", [("max_tokens", False), ("content_filtered", False), ("malformed_model_output", True)])
def test_unusable_structured_stop_reasons_raise_with_right_retryability(monkeypatch, stop, retryable):
    client, _ = make(monkeypatch, [reply(text='{"n": ', stop=stop)])
    with pytest.raises(LLMError) as exc:
        generate(client)
    assert exc.value.retryable is retryable and stop in str(exc.value)


def test_truncated_plain_text_is_returned_not_raised(monkeypatch):
    client, _ = make(monkeypatch, [reply(text="cut off mid-sen", stop="max_tokens")])
    assert client.generate(system="s", prompt="p", max_tokens=5).text == "cut off mid-sen"


def test_auto_mode_falls_back_to_forced_tool_for_nova_style_error_and_remembers(monkeypatch):
    client, fake = make(monkeypatch, [client_error("ValidationException", NOVA_ERROR), reply(tool_input={"n": 3}), reply(tool_input={"n": 4})])
    assert json.loads(generate(client).text) == {"n": 3}
    assert "outputConfig" in fake.calls[0] and "toolConfig" in fake.calls[1]
    assert fake.calls[1]["toolConfig"]["toolChoice"] == {"tool": {"name": "respond"}}
    assert json.loads(generate(client).text) == {"n": 4}
    assert len(fake.calls) == 3 and "outputConfig" not in fake.calls[2]


def test_auto_mode_does_not_swallow_unrelated_validation_errors(monkeypatch):
    client, fake = make(monkeypatch, [client_error("ValidationException", "The maximum tokens you requested exceeds the model limit of 10000.")])
    with pytest.raises(LLMError) as exc:
        generate(client)
    assert exc.value.retryable is False and len(fake.calls) == 1


def test_native_mode_never_falls_back(monkeypatch):
    client, fake = make(monkeypatch, [client_error("ValidationException", NOVA_ERROR)], mode="native")
    with pytest.raises(LLMError):
        generate(client)
    assert len(fake.calls) == 1


def test_tool_mode_never_sends_output_config(monkeypatch):
    client, fake = make(monkeypatch, [reply(tool_input={"n": 5})], mode="tool")
    assert json.loads(generate(client).text) == {"n": 5}
    assert "outputConfig" not in fake.calls[0] and "toolConfig" in fake.calls[0]


@pytest.mark.parametrize("code, retryable", [("ThrottlingException", True), ("ServiceUnavailableException", True), ("AccessDeniedException", False)])
def test_client_errors_are_classified(monkeypatch, code, retryable):
    client, _ = make(monkeypatch, [client_error(code, "boom")])
    with pytest.raises(LLMError) as exc:
        client.generate(system="s", prompt="p", max_tokens=5)
    assert exc.value.retryable is retryable


LOOP = '{"n": 1,' + "\n\t\t" * 80


def test_auto_mode_retries_once_as_tool_call_when_native_decoding_loops_on_whitespace(monkeypatch):
    client, fake = make(monkeypatch, [reply(text=LOOP, stop="max_tokens"), reply(tool_input={"n": 6})])
    result = generate(client)
    assert json.loads(result.text) == {"n": 6}
    assert "outputConfig" in fake.calls[0] and "toolConfig" in fake.calls[1]
    assert client._native_supported is True


def test_tokens_of_the_discarded_looping_call_are_billed_into_the_result(monkeypatch):
    client, _ = make(monkeypatch, [reply(text=LOOP, stop="max_tokens"), reply(tool_input={"n": 6})])
    result = generate(client)
    assert (result.prompt_tokens, result.completion_tokens) == (22, 14)


def test_native_mode_reports_the_whitespace_loop_instead_of_advising_a_bigger_budget(monkeypatch):
    client, fake = make(monkeypatch, [reply(text=LOOP, stop="max_tokens")], mode="native")
    with pytest.raises(LLMError) as exc:
        generate(client)
    assert "whitespace loop" in str(exc.value) and "will not help" in str(exc.value)
    assert exc.value.retryable is False and len(fake.calls) == 1


def test_ordinary_truncation_still_advises_raising_max_tokens(monkeypatch):
    client, fake = make(monkeypatch, [reply(text='{"n": 1, "name": "ab', stop="max_tokens")])
    with pytest.raises(LLMTruncated) as exc:
        generate(client)
    assert "raise max_tokens" in str(exc.value) and len(fake.calls) == 1


def test_auto_mode_retries_a_timed_out_native_call_once_as_a_forced_tool_call(monkeypatch):
    timeout = ReadTimeoutError(endpoint_url="https://bedrock-runtime.example")
    client, fake = make(monkeypatch, [timeout, reply(tool_input={"n": 8})])
    assert json.loads(generate(client).text) == {"n": 8}
    assert "outputConfig" in fake.calls[0] and "toolConfig" in fake.calls[1]
    assert client._native_supported is None


def test_native_mode_lets_a_timeout_surface_as_a_retryable_error(monkeypatch):
    timeout = ReadTimeoutError(endpoint_url="https://bedrock-runtime.example")
    client, fake = make(monkeypatch, [timeout], mode="native")
    with pytest.raises(LLMError) as exc:
        generate(client)
    assert exc.value.retryable is True and len(fake.calls) == 1
