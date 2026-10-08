"""The model behind the agent loop: one interface, one adapter per provider, and a scripted stand-in for tests.

Conversations use a small neutral format so any provider can sit behind `next_step`:
- {"role": "user", "text": str}
- {"role": "assistant", "text": str, "calls": [{"id", "name", "args"}], "raw": provider content or None}
- {"role": "tool", "id": str, "name": str, "result": str, "is_error": bool}
"""
import json
import os
from dataclasses import dataclass, field

import anthropic

DEFAULT_MODEL = "claude-opus-5-5"


class LLMError(RuntimeError):
    """The model call failed or returned something unusable."""


@dataclass
class Step:
    text: str = ""
    calls: list[dict] = field(default_factory=list)  # [{"id", "name", "args"}]
    raw: list | None = None  # provider content, replayed unchanged on the next turn
    tokens: int = 0

    def as_message(self) -> dict:
        return {"role": "assistant", "text": self.text, "calls": self.calls, "raw": self.raw}


class ScriptedLLM:
    """Plays back a fixed list of steps, ignoring its input. Drives tests and demos with no API spend."""

    def __init__(self, steps: list[Step]):
        self.steps, self.seen = list(steps), []

    def next_step(self, system: str, messages: list[dict], tools: list[dict]) -> Step:
        self.seen.append(messages)
        return self.steps.pop(0) if self.steps else Step(text="done")


def _to_anthropic(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            content = m.get("raw") or ([{"type": "text", "text": m["text"]}] if m.get("text") else []) + [
                {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in m.get("calls", [])]
            out.append({"role": "assistant", "content": content})
        else:
            block = {"type": "tool_result", "tool_use_id": m["id"], "content": m["result"], "is_error": m["is_error"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


class AnthropicLLM:
    def __init__(self, client=None, model: str | None = None):
        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("FIX_MODEL", DEFAULT_MODEL)

    def next_step(self, system: str, messages: list[dict], tools: list[dict]) -> Step:
        try:
            r = self.client.beta.messages.create(
                model=self.model, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                output_config={"effort": "medium"}, system=system, tools=tools, messages=_to_anthropic(messages))
        except anthropic.APIError as e:
            raise LLMError(f"the model call failed: {e.__class__.__name__}") from e
        except TypeError as e:
            raise LLMError("no Anthropic credentials: set ANTHROPIC_API_KEY") from e
        if r.stop_reason == "refusal":
            raise LLMError("the model declined the request")
        if r.stop_reason not in ("end_turn", "tool_use"):
            raise LLMError(f"unexpected stop reason {r.stop_reason}")
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "args": json.loads(json.dumps(b.input))} for b in r.content
                 if b.type == "tool_use"]
        raw = [b.model_dump(mode="json", exclude_none=True) for b in r.content]
        return Step(text=text, calls=calls, raw=raw, tokens=r.usage.input_tokens + r.usage.output_tokens)
