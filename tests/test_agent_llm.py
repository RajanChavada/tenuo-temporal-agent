import json
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest

from fixagent.agent.llm import AnthropicLLM, LLMError, ScriptedLLM, Step, _to_anthropic


def test_scripted_plays_steps_then_finishes():
    llm = ScriptedLLM([Step(text="a")])
    assert llm.next_step("s", [], []).text == "a"
    assert llm.next_step("s", [], []).text == "done"


def test_to_anthropic_groups_tool_results_and_replays_raw():
    raw = [{"type": "thinking", "thinking": "t", "signature": "sig"}, {"type": "tool_use", "id": "1", "name": "x", "input": {}}]
    msgs = [{"role": "user", "text": "hi"},
            {"role": "assistant", "text": "", "calls": [{"id": "1", "name": "x", "args": {}}], "raw": raw},
            {"role": "tool", "id": "1", "name": "x", "result": "ok", "is_error": False},
            {"role": "tool", "id": "2", "name": "y", "result": "no", "is_error": True}]
    out = _to_anthropic(msgs)
    assert out[1]["content"] == raw
    assert out[2]["role"] == "user" and [b["tool_use_id"] for b in out[2]["content"]] == ["1", "2"]
    assert out[2]["content"][1]["is_error"] is True


def test_to_anthropic_builds_content_without_raw():
    msgs = [{"role": "assistant", "text": "t", "calls": [{"id": "1", "name": "x", "args": {"a": 1}}], "raw": None}]
    assert _to_anthropic(msgs)[0]["content"] == [{"type": "text", "text": "t"},
                                                 {"type": "tool_use", "id": "1", "name": "x", "input": {"a": 1}}]


class Block(NS):
    def model_dump(self, **_):
        return {k: v for k, v in vars(self).items()}


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def response(stop="tool_use"):
    return NS(stop_reason=stop, usage=NS(input_tokens=10, output_tokens=5),
              content=[Block(type="text", text="checking"), Block(type="tool_use", id="t1", name="run_gates", input={})])


def test_anthropic_returns_calls_and_sends_fallbacks():
    client = FakeClient(response())
    step = AnthropicLLM(client, model="m").next_step("sys", [{"role": "user", "text": "hi"}], [{"name": "run_gates"}])
    assert step.calls == [{"id": "t1", "name": "run_gates", "args": {}}]
    assert step.text == "checking" and step.tokens == 15 and step.raw[1]["name"] == "run_gates"
    assert client.kwargs["fallbacks"] == "default" and client.kwargs["model"] == "m"
    assert client.kwargs["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_anthropic_bad_stop_is_llm_error(stop):
    with pytest.raises(LLMError):
        AnthropicLLM(FakeClient(response(stop)), model="m").next_step("s", [], [])


def test_anthropic_api_error_is_llm_error():
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    with pytest.raises(LLMError, match="APIConnectionError"):
        AnthropicLLM(FakeClient(error=err), model="m").next_step("s", [], [])
