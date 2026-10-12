"""Supplementary tests (not contract) — local-backend hardening.

Covers the four fixes from the local-26B audit's "safe fixes" batch:

1. LLM requests are serialized process-wide (single-slot local servers).
2. Dropped/malformed/leaked tool calls are reported instead of vanishing.
3. Ctrl+C or closed stdin at the confirmation gate cancels, not crashes
   (ISSUE-061), and Ctrl+C during a consult returns to the prompt.
4. The self-installed config.toml writes out ``[limits]`` (ISSUE-052) with
   values identical to the built-in defaults.

These live under tests/impl/ by repo convention: the implementer's own tests,
supplementary to the immutable contract suite.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import threading
import time
import urllib.request

import pytest

from sasha import config as config_mod
from sasha import llm as llm_mod
from sasha.config import Profile
from sasha.llm import ChatClient, ChatError
from sasha.repl import Repl, TerminalConfirmer
from sasha.session import Session


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _isolated_config_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class _Tracker:
    """Counts how many requests are simultaneously inside the `with` block."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def enter(self):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def exit(self):
        with self._lock:
            self.active -= 1


class _FakeResponse:
    def __init__(self, lines, tracker=None, delay=0.0):
        self._lines = lines
        self._tracker = tracker
        self._delay = delay

    def __enter__(self):
        if self._tracker is not None:
            self._tracker.enter()
        return self

    def __exit__(self, *exc):
        if self._tracker is not None:
            self._tracker.exit()
        return False

    def __iter__(self):
        for line in self._lines:
            if self._delay:
                time.sleep(self._delay)
            yield line


def _sse(obj):
    return b"data: " + json.dumps(obj).encode("utf-8") + b"\n"


_DONE = b"data: [DONE]\n"


def _text_stream(text):
    return [
        _sse({"choices": [{"delta": {"content": text}}]}),
        _DONE,
    ]


def _tool_call_stream(call_id, name, args_text):
    return [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": call_id,
             "function": {"name": name, "arguments": args_text}}]}}]}),
        _sse({"choices": [{"delta": {}}]}),
        _DONE,
    ]


def _patch_stream(monkeypatch, stream_factory):
    def fake_urlopen(req, timeout=None):
        return _FakeResponse(stream_factory())
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _client():
    return ChatClient(Profile("stub", "http://stub.test/v1", "stub-model", ""))


class _Sink:
    color_enabled = False

    def __init__(self):
        self.lines = []

    def write_line(self, text):
        self.lines.append(text)

    def ensure_newline(self):
        pass


class _Executor:
    def __init__(self, cwd):
        self.cwd = cwd


# --------------------------------------------------------------------------
# 1. request serialization
# --------------------------------------------------------------------------

def test_concurrent_llm_requests_are_serialized(monkeypatch):
    """Four threads calling chat() must never overlap inside the request."""
    tracker = _Tracker()

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(_text_stream("hi"), tracker, delay=0.02)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = _client()
    errors = []

    def worker():
        try:
            client.chat([{"role": "user", "content": "q"}], [], lambda t: None)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []
    assert tracker.max_active == 1


def test_request_lock_is_released_after_a_stream_error(monkeypatch):
    """A failed request must not leak the lock and wedge the process."""
    state = {"n": 0}

    def fake_urlopen(req, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            return _FakeResponse([b"data: {this is not json\n"])
        return _FakeResponse(_text_stream("ok"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = _client()
    with pytest.raises(ChatError):
        client.chat([], [], lambda t: None)

    if llm_mod._GATE._busy:
        pytest.fail("request gate leaked after a stream error")

    assert client.chat([], [], lambda t: None).text == "ok"


# --------------------------------------------------------------------------
# 2. loud tool-call degradation
# --------------------------------------------------------------------------

def test_malformed_tool_arguments_warn_and_drop(monkeypatch):
    _patch_stream(monkeypatch, lambda: _tool_call_stream(
        "call_1", "run_command", "{this is not json"))

    warnings = []
    resp = _client().chat([], [], lambda t: None, on_warning=warnings.append)

    assert resp.tool_calls == []          # still dropped, never guessed
    assert len(warnings) == 1
    assert "not valid JSON" in warnings[0]
    assert "run_command" in warnings[0]


def test_incomplete_tool_call_warns_and_drops(monkeypatch):
    # id present, function name never sent -> cannot be dispatched
    def stream():
        return [
            _sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1",
                 "function": {"arguments": "{}"}}]}}]}),
            _DONE,
        ]

    _patch_stream(monkeypatch, stream)

    warnings = []
    resp = _client().chat([], [], lambda t: None, on_warning=warnings.append)

    assert resp.tool_calls == []
    assert len(warnings) == 1
    assert "incomplete tool call" in warnings[0]


def test_leaked_special_token_tool_call_warns(monkeypatch):
    """ISSUE-060: the local profile emits native tool-call tokens as text."""
    leaked = ("<" + "\uff5ctool\u2581calls\u2581begin\uff5c"
              + "function\uff5crun_command\uff5c")
    _patch_stream(monkeypatch, lambda: _text_stream(leaked))

    warnings = []
    resp = _client().chat([], [], lambda t: None, on_warning=warnings.append)

    assert resp.tool_calls == []
    assert len(warnings) == 1
    assert "tool_calls delta" in warnings[0]
    assert "ISSUE-060" in warnings[0]


def test_no_warning_callback_is_still_silent_and_safe(monkeypatch):
    """The default path is unchanged for every existing caller."""
    _patch_stream(monkeypatch, lambda: _tool_call_stream(
        "call_1", "run_command", "{broken"))

    resp = _client().chat([], [], lambda t: None)

    assert resp.tool_calls == []


def test_healthy_tool_call_produces_no_warning(monkeypatch):
    _patch_stream(monkeypatch, lambda: _tool_call_stream(
        "call_1", "run_command", json.dumps({"command": "ls", "description": "d"})))

    warnings = []
    resp = _client().chat([], [], lambda t: None, on_warning=warnings.append)

    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "run_command"
    assert warnings == []


# --------------------------------------------------------------------------
# 3. Ctrl+C / EOF at the gate (ISSUE-061)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("exc", [KeyboardInterrupt, EOFError])
def test_confirmer_declines_on_interrupt(exc):
    sink = _Sink()

    def boom(prompt):
        raise exc()

    verdict, payload = TerminalConfirmer(boom, sink).ask("rm -rf /", "danger")

    assert (verdict, payload) == ("no", "")
    assert any("cancelled" in line for line in sink.lines)


@pytest.mark.parametrize("exc", [KeyboardInterrupt, EOFError])
def test_confirmer_edit_prompt_interrupt_also_declines(exc):
    sink = _Sink()
    responses = iter(["e"])

    def input_fn(prompt):
        try:
            return next(responses)
        except StopIteration:
            raise exc()

    verdict, _ = TerminalConfirmer(input_fn, sink).ask("ls", "list")

    assert verdict == "no"


def test_loop_continues_after_keyboard_interrupt_inside_run_once(
        monkeypatch, tmp_path):
    _isolated_config_env(monkeypatch, tmp_path)
    from sasha import repl as repl_mod

    cfg = dataclasses.replace(config_mod.load(None),
                              directory_recap_enabled=False)
    monkeypatch.setattr(repl_mod.memory, "resolve_git_root", lambda cwd: None)

    sink = _Sink()
    inputs = iter(["first", "second"])

    def input_fn(prompt):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    repl = Repl(cfg, _Executor(str(tmp_path)), None,
                Session(cfg.limits.history_tokens),
                client_factory=lambda p: None, confirmer=None,
                input_fn=input_fn, sink=sink)

    calls = {"n": 0}

    def fake_run_once(line):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt
        return True

    monkeypatch.setattr(repl, "run_once", fake_run_once)

    repl.loop()                      # must return, not raise

    assert calls["n"] == 2           # the loop really did continue


# --------------------------------------------------------------------------
# 4. [limits] self-install (ISSUE-052)
# --------------------------------------------------------------------------

def test_self_installed_config_writes_limits(monkeypatch, tmp_path):
    _isolated_config_env(monkeypatch, tmp_path)

    cfg = config_mod.load(None)
    text = cfg.path.read_text()

    assert "[limits]" in text
    for key in ("history_tokens", "stderr_capture_bytes",
                "max_tool_turns", "mute_threshold"):
        assert key in text


def test_self_installed_limits_equal_builtin_defaults(monkeypatch, tmp_path):
    """Self-install must stay behaviourally a no-op."""
    _isolated_config_env(monkeypatch, tmp_path)

    first = config_mod.load(None)
    assert first.limits == config_mod.Limits()

    # ...and the written file re-reads to the same values.
    second = config_mod.load(None)
    assert second.limits == config_mod.Limits()
    assert second.path == first.path


# --------------------------------------------------------------------------
# foreground priority: optional background work must never delay the user
# --------------------------------------------------------------------------

def _counting_stream(monkeypatch, on_enter=None, gate_event=None):
    """urlopen replacement that records concurrency and can hold a request
    open so a competing request can be observed against it."""
    tracker = _Tracker()
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if on_enter is not None:
            on_enter(calls["n"])

        class _Holder(_FakeResponse):
            def __enter__(self):
                tracker.enter()
                if gate_event is not None:
                    gate_event.wait(timeout=5)
                return self

            def __exit__(self, *exc):
                tracker.exit()
                return False

        return _Holder(_text_stream("ok"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls, tracker


def test_background_request_is_skipped_while_a_foreground_one_runs(monkeypatch):
    inside = threading.Event()
    release = threading.Event()

    def on_enter(n):
        if n == 1:
            inside.set()

    calls, _ = _counting_stream(monkeypatch, on_enter=on_enter,
                                gate_event=release)
    client = _client()

    fg = threading.Thread(target=lambda: client.chat([], [], lambda t: None))
    fg.start()
    assert inside.wait(timeout=5)          # foreground is inside the request

    with llm_mod.background_request():
        resp = client.chat([], [], lambda t: None)

    assert resp.text == "" and resp.tool_calls == []   # skipped
    assert calls["n"] == 1                              # never hit the wire

    release.set()
    fg.join(timeout=10)


def test_background_request_is_skipped_while_a_foreground_one_waits(monkeypatch):
    inside = threading.Event()
    release = threading.Event()

    calls, _ = _counting_stream(monkeypatch,
                                on_enter=lambda n: inside.set() if n == 1 else None,
                                gate_event=release)
    client = _client()

    fg1 = threading.Thread(target=lambda: client.chat([], [], lambda t: None))
    fg1.start()
    assert inside.wait(timeout=5)

    fg2 = threading.Thread(target=lambda: client.chat([], [], lambda t: None))
    fg2.start()
    deadline = time.time() + 5
    while time.time() < deadline and llm_mod._GATE._foreground_waiting == 0:
        time.sleep(0.01)
    assert llm_mod._GATE._foreground_waiting == 1        # fg2 is queued

    with llm_mod.background_request():
        resp = client.chat([], [], lambda t: None)

    assert resp.text == ""
    assert calls["n"] == 1                               # still only fg1

    release.set()
    fg1.join(timeout=10)
    fg2.join(timeout=10)


def test_background_request_abandons_mid_stream_when_the_user_asks(monkeypatch):
    """The whole point: a streaming background nicety gets out of the way."""
    first_line = threading.Event()
    user_arrived = threading.Event()
    state = {"n": 0}

    def abandoning_stream():
        yield _sse({"choices": [{"delta": {"content": "background partial"}}]})
        first_line.set()
        user_arrived.wait(timeout=5)
        yield _sse({"choices": [{"delta": {"content": " and more"}}]})
        yield _DONE

    def fake_urlopen(req, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            return _FakeResponse(abandoning_stream())
        return _FakeResponse(_text_stream("the user's answer"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _client()
    got = {}
    bg_text = []

    def background():
        with llm_mod.background_request():
            got["bg"] = client.chat([], [], bg_text.append)

    thread = threading.Thread(target=background)
    thread.start()
    assert first_line.wait(timeout=5)

    fg = threading.Thread(
        target=lambda: got.__setitem__("fg", client.chat([], [], lambda t: None)))
    fg.start()
    deadline = time.time() + 5
    while time.time() < deadline and llm_mod._GATE._foreground_waiting == 0:
        time.sleep(0.01)
    user_arrived.set()

    thread.join(timeout=10)
    fg.join(timeout=10)

    # it really had started streaming...
    assert "background partial" in "".join(bg_text)
    # ...and still yielded to the user, reporting "nothing to say"
    assert got["bg"].text == "" and got["bg"].tool_calls == []
    assert got["fg"].text == "the user's answer"


def test_background_marker_is_restored_even_on_error():
    assert getattr(llm_mod._priority, "value", "foreground") == "foreground"
    with pytest.raises(RuntimeError):
        with llm_mod.background_request():
            raise RuntimeError("boom")
    assert getattr(llm_mod._priority, "value", "foreground") == "foreground"


def test_foreground_requests_never_skip(monkeypatch):
    """Two concurrent foreground requests must both run, serialized."""
    calls, tracker = _counting_stream(monkeypatch)
    client = _client()
    results = []

    def worker():
        results.append(client.chat([], [], lambda t: None).text)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results == ["ok", "ok", "ok"]
    assert calls["n"] == 3
    assert tracker.max_active == 1
