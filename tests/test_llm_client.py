"""AnthropicClient request shape and response parsing, against a fake SDK client (no network)."""
from types import SimpleNamespace

import pytest

from cuauto.agent.llm import AnthropicClient


class Block(SimpleNamespace):
    def model_dump(self, exclude_none: bool = False) -> dict:
        return {k: v for k, v in vars(self).items() if not (exclude_none and v is None)}


def tool_use(name="click", input=None, id="toolu_1"):
    return Block(type="tool_use", id=id, name=name, input=input if input is not None else {"ref": "e1"})


class FakeMessages:
    def __init__(self, content):
        self.content, self.calls = content, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.content, usage=SimpleNamespace(input_tokens=11, output_tokens=7))


def make_client(content) -> tuple[AnthropicClient, FakeMessages]:
    llm = AnthropicClient.__new__(AnthropicClient)  # skip __init__: it would build a real SDK client
    llm.name, llm.max_tokens = "claude-sonnet-5-5", 16000
    fake = FakeMessages(content)
    llm.client = SimpleNamespace(messages=fake)
    return llm, fake


def decide(llm):
    return llm.decide("system", [{"role": "user", "content": "obs"}], [{"name": "click"}], None)


def test_request_uses_auto_tool_choice_and_summarized_thinking():
    llm, fake = make_client([tool_use()])
    decide(llm)
    (call,) = fake.calls
    # forced tool_choice (any/tool) is rejected by current models with a 400
    assert call["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert call["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert call["max_tokens"] == 16000
    assert call["model"] == "claude-sonnet-5-5"


def test_default_max_tokens_leaves_room_for_thinking():
    import inspect
    assert inspect.signature(AnthropicClient.__init__).parameters["max_tokens"].default == 16000


def test_reasoning_prefers_thinking_summary_and_text():
    llm, _ = make_client([Block(type="thinking", thinking="Need the member search first.", signature="sig"),
                          Block(type="text", text="Opening inquiry."),
                          tool_use(input={"ref": "e1", "why": "Open Member Inquiry"})])
    d = decide(llm)
    assert d.text == "Need the member search first. Opening inquiry."
    assert (d.tool, d.input, d.tool_use_id) == ("click", {"ref": "e1", "why": "Open Member Inquiry"}, "toolu_1")
    assert d.usage == {"input_tokens": 11, "output_tokens": 7}


def test_reasoning_falls_back_to_tool_why_when_model_did_not_think():
    llm, _ = make_client([Block(type="thinking", thinking="", signature="sig"),
                          tool_use(input={"ref": "e3", "why": "View member"})])
    assert decide(llm).text == "View member"


def test_reasoning_empty_when_no_thinking_text_or_why():
    llm, _ = make_client([tool_use(name="done", input={"summary": "ok"})])
    assert decide(llm).text == ""


def test_assistant_content_keeps_text_and_first_tool_use_only():
    llm, _ = make_client([Block(type="thinking", thinking="t", signature="sig"),
                          Block(type="text", text="a"),
                          tool_use(id="toolu_1"), tool_use(id="toolu_2")])
    d = decide(llm)
    assert [(b["type"], b.get("id")) for b in d.assistant_content] == [("text", None), ("tool_use", "toolu_1")]


def test_no_tool_call_raises():
    llm, _ = make_client([Block(type="text", text="I think we are done.")])
    with pytest.raises(RuntimeError, match="no tool call"):
        decide(llm)
