"""Contract tests — Task 2.1: streaming chat client.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-2.1-llm.md (design §5.4, §7.1).
Offline-safe: all traffic goes to the in-process SSE stub.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer, refused_base_url  # noqa: E402

SOME_TOOLS = [{"type": "function", "function": {"name": "run_command",
               "description": "d", "parameters": {"type": "object"}}}]


def _profile(base_url, api_key=""):
    from sasha.config import Profile

    return Profile(name="stub", base_url=base_url, model="stub-model",
                   api_key=api_key)


def _client(base_url, api_key=""):
    from sasha.llm import ChatClient

    return ChatClient(_profile(base_url, api_key), timeout_s=10.0)


MSGS = [{"role": "user", "content": "hi"}]


# --- AC1: incremental ordered text -------------------------------------------

def test_ac1_text_streams_incrementally():
    with StubServer([{"kind": "text",
                      "text": "Hello streaming world!", "chunk_size": 5}]) as stub:
        got = []
        resp = _client(stub.base_url).chat(MSGS, [], on_text=got.append)
    assert resp.text == "Hello streaming world!"
    assert "".join(got) == "Hello streaming world!"
    assert len(got) >= 2  # incremental, not one blob
    assert resp.tool_calls == []


# --- AC2: fragmented tool call ------------------------------------------------

def test_ac2_fragmented_tool_call_assembled():
    args = {"command": "ls -la /var/log", "description": "list the logs"}
    with StubServer([{"kind": "tool_call", "id": "call_42",
                      "name": "run_command", "arguments": args,
                      "fragments": 4}]) as stub:
        resp = _client(stub.base_url).chat(MSGS, SOME_TOOLS, on_text=lambda t: None)
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.id == "call_42"
    assert call.name == "run_command"
    assert call.arguments == args  # parsed dict, reassembled from fragments


# --- AC3: Authorization header iff key ------------------------------------------

def test_ac3_bearer_header_sent_with_key():
    with StubServer([{"kind": "text", "text": "ok"}]) as stub:
        _client(stub.base_url, api_key="sk-test").chat(MSGS, [], lambda t: None)
        assert stub.requests[0]["headers"].get("authorization") == "Bearer sk-test"


def test_ac3_no_header_without_key():
    with StubServer([{"kind": "text", "text": "ok"}]) as stub:
        _client(stub.base_url, api_key="").chat(MSGS, [], lambda t: None)
        assert "authorization" not in stub.requests[0]["headers"]


# --- AC4: error vocabulary ------------------------------------------------------

def test_ac4_connection_refused_raises_chaterror():
    from sasha.llm import ChatError

    with pytest.raises(ChatError) as exc:
        _client(refused_base_url()).chat(MSGS, [], lambda t: None)
    assert exc.value.reason


def test_ac4_http_error_raises_chaterror():
    from sasha.llm import ChatError

    with StubServer([{"kind": "http_error", "status": 500}]) as stub:
        with pytest.raises(ChatError) as exc:
            _client(stub.base_url).chat(MSGS, [], lambda t: None)
    assert exc.value.reason


def test_ac4_midstream_disconnect_raises_chaterror():
    from sasha.llm import ChatError

    with StubServer([{"kind": "disconnect", "text": "par"}]) as stub:
        with pytest.raises(ChatError) as exc:
            _client(stub.base_url).chat(MSGS, [], lambda t: None)
    assert exc.value.reason


# --- AC5: malformed tool arguments degrade to text --------------------------------

def test_ac5_bad_tool_json_degrades_to_text():
    with StubServer([{"kind": "tool_call", "id": "call_1",
                      "name": "run_command", "arguments": "{this is not json",
                      "text": "prefix words", "fragments": 2}]) as stub:
        resp = _client(stub.base_url).chat(MSGS, SOME_TOOLS, on_text=lambda t: None)
    assert resp.tool_calls == []            # dropped, never guessed
    assert "prefix words" in resp.text      # streamed text preserved


# --- AC6: request body shape ---------------------------------------------------------

def test_ac6_body_carries_model_messages_stream_tools():
    with StubServer([{"kind": "text", "text": "ok"}]) as stub:
        _client(stub.base_url).chat(MSGS, SOME_TOOLS, lambda t: None)
        body = stub.requests[0]["body"]
    assert body["model"] == "stub-model"
    assert body["messages"] == MSGS
    assert body["stream"] is True
    assert body["tools"] == SOME_TOOLS


def test_ac6_empty_tools_omitted():
    with StubServer([{"kind": "text", "text": "ok"}]) as stub:
        _client(stub.base_url).chat(MSGS, [], lambda t: None)
        body = stub.requests[0]["body"]
    assert not body.get("tools")
