"""Contract tests — Task 10.1: boot-time profile + tip hint (ISSUE-027).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-10.1-boot-hint.md
"""


class _SpySink:
    color_enabled = False

    def __init__(self, events):
        self._events = events

    def write_line(self, text):
        self._events.append(text)

    def write_chunk(self, text):
        if text:
            self._events.append(text)

    def ensure_newline(self):
        if self._events and isinstance(self._events[-1], str) \
                and not self._events[-1].endswith("\n"):
            self._events.append("\n")


class _CwdExecutor:
    def __init__(self, cwd):
        self.cwd = cwd


class _EOFInput:
    """Records each prompt request as ("prompt", text), then ends the loop."""

    def __init__(self, events):
        self._events = events

    def __call__(self, prompt):
        self._events.append(("prompt", prompt))
        raise EOFError


def _repl(events, cwd="/home/testuser"):
    from sasha.config import Config, Limits, Profile
    from sasha.repl import Repl
    from sasha.session import Session

    profiles = {
        "deepseek": Profile(name="deepseek",
                             base_url="https://api.deepseek.com/v1",
                             model="deepseek-chat", api_key=""),
    }
    config = Config(default_profile="deepseek", profiles=profiles,
                     limits=Limits())
    sink = _SpySink(events)
    input_fn = _EOFInput(events)
    repl = Repl(config, _CwdExecutor(cwd), None, Session(), lambda p: None,
                None, input_fn, sink)
    return repl


# --- AC1: profile line is the first write_line call -------------------------

def test_ac1_profile_line_is_first():
    events = []
    _repl(events).loop()
    assert events[0] == ("[sasha] profile: deepseek "
                          "(deepseek-chat @ https://api.deepseek.com/v1)")


def test_ac1_reflects_whatever_profile_is_current():
    from sasha.config import Profile

    events = []
    repl = _repl(events)
    repl._current_profile = Profile(name="local", base_url="http://x/v1",
                                     model="local-model", api_key="")
    repl.loop()
    assert events[0] == "[sasha] profile: local (local-model @ http://x/v1)"


# --- AC2: tip line is the second write_line call -----------------------------

def test_ac2_tip_line_is_second():
    events = []
    _repl(events).loop()
    assert events[1] == ("[sasha] tip: type ?? followed by a question "
                          "to chat with me directly")


# --- AC3: both lines precede the first prompt request ------------------------

def test_ac3_both_lines_precede_first_prompt():
    events = []
    _repl(events).loop()
    prompt_indices = [i for i, e in enumerate(events)
                      if isinstance(e, tuple) and e[0] == "prompt"]
    assert prompt_indices, "expected at least one prompt request"
    assert prompt_indices[0] == 2


def test_ac3_only_two_lines_written_before_first_prompt():
    events = []
    _repl(events).loop()
    before_prompt = [e for e in events if isinstance(e, str)]
    assert len(before_prompt) == 2


# --- AC4: existing `profile` verb output is unchanged ------------------------

def test_ac4_profile_verb_no_args_unchanged():
    from sasha.config import Config, Limits, Profile
    from sasha.repl import Repl
    from sasha.session import Session

    events = []
    profiles = {"stubprof": Profile(name="stubprof", base_url="http://x/v1",
                                    model="stub-model", api_key="")}
    config = Config(default_profile="stubprof", profiles=profiles,
                     limits=Limits())
    sink = _SpySink(events)
    repl = Repl(config, _CwdExecutor("/tmp"), None, Session(),
                lambda p: None, None, lambda prompt: "", sink)

    repl.run_once("sasha profile")

    assert events == ["[sasha] profile: stubprof (stub-model @ http://x/v1)"]


def test_ac4_profile_verb_switch_unchanged():
    from sasha.config import Config, Limits, Profile
    from sasha.repl import Repl
    from sasha.session import Session

    events = []
    profiles = {
        "stubprof": Profile(name="stubprof", base_url="http://x/v1",
                             model="stub-model", api_key=""),
        "other": Profile(name="other", base_url="http://y/v1",
                          model="other-model", api_key=""),
    }
    config = Config(default_profile="stubprof", profiles=profiles,
                     limits=Limits())
    sink = _SpySink(events)
    repl = Repl(config, _CwdExecutor("/tmp"), None, Session(),
                lambda p: None, None, lambda prompt: "", sink)

    repl.run_once("sasha profile other")

    assert events == ["[sasha] profile: other (other-model @ http://y/v1)"]
