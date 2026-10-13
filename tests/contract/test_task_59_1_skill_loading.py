"""Contract tests — Task 59.1: sasha skill loading (ISSUE-064, stage 1).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-59.1-skill-loading.md
Design: docs/superpowers/specs/2026-08-08-issue-064-skill-loading-design.md
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


class SpyExecutor:
    def __init__(self, cwd):
        self._cwd = cwd
        self.calls = []

    @property
    def cwd(self):
        return self._cwd

    def run(self, command, capture_stdout=False):
        raise AssertionError("executor.run must not be called for use_skill")


class RefusingConfirmer:
    """Spy that asserts it is never called — use_skill is auto-approved."""

    def ask(self, summary, description):
        raise AssertionError("confirmer.ask must not be called for use_skill")


class ScriptedConfirmer:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.asked = []

    def ask(self, summary, description):
        self.asked.append((summary, description))
        return self.verdicts.pop(0)


def _tc(name, arguments, call_id="call_1"):
    return {"kind": "tool_call", "id": call_id, "name": name,
            "arguments": arguments, "fragments": 2}


def _consult(stub_url, confirmer, executor=None, seed="help me", max_tool_turns=10):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    session = Session()
    session.add_user(seed)
    client = ChatClient(Profile(name="stub", base_url=stub_url,
                                model="stub-model", api_key=""), timeout_s=10.0)
    if executor is None:
        executor = SpyExecutor("/spy")
    out_chunks = []
    run_consult(session, client, executor, confirmer,
                out=out_chunks.append, max_tool_turns=max_tool_turns)
    return session, executor, confirmer, "".join(out_chunks)


def _tool_message_contents(stub, request_index):
    msgs = stub.requests[request_index]["body"]["messages"]
    return [m["content"] for m in msgs if m.get("role") == "tool"]


def _log_path(tmp_path) -> pathlib.Path:
    return tmp_path / "xdg-state" / "sasha" / "tool_log.jsonl"


def _read_entries(tmp_path) -> list[dict]:
    path = _log_path(tmp_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _write_skill(root: pathlib.Path, marketplace: str, plugin: str, version: str,
                  skill_dir: str, frontmatter: str | None, body: str = "Body text.\n"):
    skill_path = (root / marketplace / plugin / version / "skills" / skill_dir
                  / "SKILL.md")
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    content = (frontmatter + body) if frontmatter is not None else body
    skill_path.write_text(content)
    return skill_path


_FM_BRAINSTORMING = (
    "---\n"
    "name: brainstorming\n"
    "description: Explore requirements before implementation.\n"
    "---\n"
)


# --- AC1: exact schema --------------------------------------------------------

def test_ac1_use_skill_schema_exact():
    from sasha.tools import REGISTRY
    from sasha import skills
    assert REGISTRY["use_skill"].schema == skills.USE_SKILL_SCHEMA
    assert skills.USE_SKILL_SCHEMA == {
        "type": "function",
        "function": {
            "name": "use_skill",
            "description": (
                # Amended by Tier 1 (2026-10-12): a live probe against the
                # local 26B caught it calling use_skill(name="remember") --
                # mistaking a tool for a skill. The description now says the
                # name must come from the listed skills, and that this only
                # reads (remembering is the `remember` tool's job).
                "Load one of the skills listed under 'Available skills' in your "
                "instructions, using the exact name shown there. A skill is a "
                "packaged set of instructions for a specific kind of task. This "
                "only READS a listed skill: it cannot save, store or remember "
                "anything — the `remember` tool does that — and a name that is not "
                "in the list will fail."),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "args": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    }


# --- AC2-AC4: _parse_frontmatter() --------------------------------------------

def test_ac2_parse_frontmatter_valid():
    from sasha.skills import _parse_frontmatter
    text = "---\nname: brainstorming\ndescription: Explore first.\n---\nBody.\n"
    assert _parse_frontmatter(text) == {
        "name": "brainstorming", "description": "Explore first."}


def test_ac3_parse_frontmatter_missing_block():
    from sasha.skills import _parse_frontmatter
    assert _parse_frontmatter("Just a body, no frontmatter.\n") is None


def test_ac4_parse_frontmatter_unclosed_block():
    from sasha.skills import _parse_frontmatter
    text = "---\nname: brainstorming\ndescription: Explore first.\nBody with no closer.\n"
    assert _parse_frontmatter(text) is None


# --- AC5-AC9: _discover() ------------------------------------------------------

def test_ac5_discover_nested_shape_frontmatter_name(tmp_path):
    from sasha.skills import _discover
    path = _write_skill(tmp_path, "superpowers-marketplace", "superpowers", "6.2.0",
                         "brainstorming", _FM_BRAINSTORMING)
    assert _discover(tmp_path) == {"brainstorming": path}


def test_ac6_discover_fallback_to_dirname(tmp_path):
    from sasha.skills import _discover
    path = _write_skill(tmp_path, "mkt", "plugin", "1.0.0", "my-skill-dir",
                         frontmatter=None, body="No frontmatter at all.\n")
    assert _discover(tmp_path) == {"my-skill-dir": path}


def test_ac7_discover_latest_semver_wins_numerically(tmp_path):
    from sasha.skills import _discover
    _write_skill(tmp_path, "mkt", "plugin", "6.2.0", "sk",
                 "---\nname: sk\ndescription: old.\n---\n")
    newest = _write_skill(tmp_path, "mkt", "plugin", "6.10.0", "sk",
                          "---\nname: sk\ndescription: new.\n---\n")
    result = _discover(tmp_path)
    assert result == {"sk": newest}


def test_ac8_discover_unparseable_version_sorts_last(tmp_path):
    from sasha.skills import _discover
    numeric = _write_skill(tmp_path, "mkt", "plugin", "1.0.0", "sk",
                           "---\nname: sk\ndescription: numeric.\n---\n")
    _write_skill(tmp_path, "mkt", "plugin", "dev", "sk",
                 "---\nname: sk\ndescription: dev build.\n---\n")
    result = _discover(tmp_path)
    assert result == {"sk": numeric}


def test_ac9_discover_name_collision_alphabetical_path_wins(tmp_path):
    from sasha.skills import _discover
    path_a = _write_skill(tmp_path, "aaa-marketplace", "plugin", "1.0.0", "dup",
                          "---\nname: dup\ndescription: from a.\n---\n")
    _write_skill(tmp_path, "zzz-marketplace", "plugin", "1.0.0", "dup",
                 "---\nname: dup\ndescription: from z.\n---\n")
    result = _discover(tmp_path)
    assert result == {"dup": path_a}


# --- AC10-AC13: build_skills_listing() ----------------------------------------

def test_ac10_listing_empty_when_root_absent(monkeypatch, tmp_path):
    from sasha import skills
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert skills.build_skills_listing() == ""


def test_ac11_listing_empty_when_no_skill_qualifies(monkeypatch, tmp_path):
    from sasha import skills
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    _write_skill(xdg / "sasha" / "skills", "mkt", "plugin", "1.0.0", "sk",
                 frontmatter=None, body="No frontmatter.\n")
    assert skills.build_skills_listing() == ""


def test_ac12_listing_formatted_and_sorted(monkeypatch, tmp_path):
    from sasha import skills
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    root = xdg / "sasha" / "skills"
    _write_skill(root, "mkt", "plugin", "1.0.0", "zeta",
                 "---\nname: zeta\ndescription: Zeta skill.\n---\n")
    _write_skill(root, "mkt", "plugin2", "1.0.0", "alpha",
                 "---\nname: alpha\ndescription: Alpha skill.\n---\n")
    assert skills.build_skills_listing() == (
        "Available skills:\n"
        "- alpha: Alpha skill.\n"
        "- zeta: Zeta skill.\n\n"
        "Use the `use_skill` tool to load one when it applies.")


def test_ac13_undescribed_skill_loadable_not_listed(monkeypatch, tmp_path):
    from sasha import skills
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    root = xdg / "sasha" / "skills"
    _write_skill(root, "mkt", "plugin", "1.0.0", "undescribed",
                 "---\nname: undescribed\n---\n", body="Body only.\n")
    assert skills.build_skills_listing() == ""
    assert skills.load_skill("undescribed") == "---\nname: undescribed\n---\nBody only.\n"


# --- AC14-AC18: load_skill() ---------------------------------------------------

def test_ac14_load_skill_unknown_name(monkeypatch, tmp_path):
    from sasha import skills
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert skills.load_skill("nope") is None


def test_ac15_load_skill_verbatim_including_frontmatter(monkeypatch, tmp_path):
    from sasha import skills
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    root = xdg / "sasha" / "skills"
    _write_skill(root, "mkt", "plugin", "1.0.0", "brainstorming",
                 _FM_BRAINSTORMING, body="Ask one thing at a time.\n")
    assert skills.load_skill("brainstorming") == (
        _FM_BRAINSTORMING + "Ask one thing at a time.\n")


def test_ac16_load_skill_args_appended_when_present(monkeypatch, tmp_path):
    from sasha import skills
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    root = xdg / "sasha" / "skills"
    _write_skill(root, "mkt", "plugin", "1.0.0", "brainstorming",
                 _FM_BRAINSTORMING, body="Body.\n")
    result = skills.load_skill("brainstorming", "focus on caching")
    assert result == _FM_BRAINSTORMING + "Body.\n\n\nARGUMENTS: focus on caching"


def test_ac17_load_skill_args_omitted_when_empty(monkeypatch, tmp_path):
    from sasha import skills
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    root = xdg / "sasha" / "skills"
    _write_skill(root, "mkt", "plugin", "1.0.0", "brainstorming",
                 _FM_BRAINSTORMING, body="Body.\n")
    result = skills.load_skill("brainstorming", "")
    assert result == _FM_BRAINSTORMING + "Body.\n"
    assert "ARGUMENTS" not in result


def test_ac18_load_skill_truncation(monkeypatch, tmp_path):
    from sasha import skills
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    root = xdg / "sasha" / "skills"
    big_body = "x" * 50000
    _write_skill(root, "mkt", "plugin", "1.0.0", "huge",
                 frontmatter=None, body=big_body)
    result = skills.load_skill("huge")
    assert len(result) == skills._MAX_SKILL_CHARS + len(
        f"\n... [truncated at {skills._MAX_SKILL_CHARS} characters]")
    assert result.endswith(f"\n... [truncated at {skills._MAX_SKILL_CHARS} characters]")
    assert result.startswith("x" * 100)


# --- AC19-AC21: agent-loop dispatch --------------------------------------------

def test_ac19_use_skill_unknown_name_tool_result(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    confirmer = RefusingConfirmer()
    with StubServer([_tc("use_skill", {"name": "nope"}),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer)
        tool_contents = _tool_message_contents(stub, 1)
    assert tool_contents == ["no such skill: nope"]


def test_ac20_use_skill_auto_approved_dispatch(monkeypatch, tmp_path):
    from sasha import skills
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    root = xdg / "sasha" / "skills"
    _write_skill(root, "mkt", "plugin", "1.0.0", "brainstorming",
                 _FM_BRAINSTORMING, body="Ask one thing at a time.\n")
    confirmer = RefusingConfirmer()
    with StubServer([_tc("use_skill", {"name": "brainstorming"}),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer)
        tool_contents = _tool_message_contents(stub, 1)
    assert tool_contents == [skills.load_skill("brainstorming")]


def test_ac21_schema_sent_includes_use_skill(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    with StubServer([{"kind": "text", "text": "hi"}]) as stub:
        _consult(stub.base_url, ScriptedConfirmer([]))
        sent_tools = stub.requests[0]["body"]["tools"]
    names = {t["function"]["name"] for t in sent_tools}
    from sasha.tools import REGISTRY
    assert names == set(REGISTRY)
    assert "use_skill" in names


# --- AC22: unknown-tool logging -------------------------------------------------

def test_ac22_unknown_tool_logs_tool_log_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    confirmer = ScriptedConfirmer([])
    with StubServer([_tc("totally_bogus_tool", {"foo": "bar"}, "call_x"),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer)

    entries = _read_entries(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["tool"] == "totally_bogus_tool"
    assert e["call_id"] == "call_x"
    assert e["arguments"] == {"foo": "bar"}
    assert e["verdict"] == "unknown_tool"
    assert e["executed"] is False
    assert e["result"] is None
    assert e["result_omitted_reason"] is None


# --- AC23-AC24: session.py composition ------------------------------------------

def test_ac23_skills_listing_present_and_positioned():
    from sasha.session import Session
    s = Session(personality="My personality.", skills_listing="Available skills:\n- x: y")
    s.set_project_memory("some project memory")
    prompt = s.system_prompt
    assert "Available skills:\n- x: y" in prompt
    personality_idx = prompt.index("My personality.")
    memory_idx = prompt.index("some project memory")
    skills_idx = prompt.index("Available skills:\n- x: y")
    functional_idx = prompt.index("You are Sasha, an interactive shell wrapper.")
    assert personality_idx < memory_idx < skills_idx < functional_idx


def test_ac24_skills_listing_absent_when_empty():
    from sasha.session import Session
    with_empty = Session(personality="P", skills_listing="")
    without_arg = Session(personality="P")
    assert with_empty.system_prompt == without_arg.system_prompt
    assert "Available skills" not in with_empty.system_prompt
