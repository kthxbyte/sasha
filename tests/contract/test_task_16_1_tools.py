"""Contract tests — Task 16.1: tools.py registry (5 tools + sandboxing).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-16.1-tools-registry.md
Design: docs/superpowers/specs/2026-07-17-multi-tool-registry-design.md
"""

import os
import pathlib

import pytest

from sasha.executor import ExecResult


class FakeExecutor:
    """Duck-typed executor: records run() calls, returns a scripted result."""

    def __init__(self, cwd):
        self._cwd = cwd
        self.calls = []

    @property
    def cwd(self):
        return self._cwd

    def run(self, command, capture_stdout=False):
        self.calls.append((command, capture_stdout))
        return ExecResult(exit_code=0, signal=None, stderr_text="err",
                           stdout_text="out", cwd_after=self._cwd, duration_ms=1)


# --- AC: registry shape -----------------------------------------------------

def test_ac_registry_has_exactly_five_tools():
    # Amended by Tier 1 for Task 17.2 (project memory): a sixth tool,
    # "remember", is added to the registry. See
    # docs/superpowers/specs/task-17.2-remember-tool.md.
    # Amended by Tier 1 for Task 57.1: a seventh tool, "question", is
    # added. See docs/superpowers/specs/task-57.1-question-core.md.
    # Amended by Tier 1 for Task 59.1: an eighth tool, "use_skill", is
    # added. See docs/superpowers/specs/task-59.1-skill-loading.md.
    from sasha.tools import REGISTRY
    assert set(REGISTRY) == {
        "run_command", "write_file", "read_file", "list_dir",
        "search_files", "remember", "question", "use_skill"}


def test_ac_permission_fixed_by_tool_type():
    from sasha.tools import REGISTRY
    # Amended by Tier 1 for Task 21.1 (safe command auto-approval,
    # ISSUE-041): run_command's requires_confirmation becomes a per-call
    # classifier instead of a fixed bool -- the one deliberate exception to
    # this "fixed by tool type" invariant. Every other tool keeps a fixed
    # bool, unchanged. See
    # docs/superpowers/specs/task-21.1-safe-command-auto-approval.md.
    assert callable(REGISTRY["run_command"].requires_confirmation)
    assert REGISTRY["write_file"].requires_confirmation is True
    assert REGISTRY["read_file"].requires_confirmation is False
    assert REGISTRY["list_dir"].requires_confirmation is False
    assert REGISTRY["search_files"].requires_confirmation is False
    for name in ("read_file", "list_dir", "search_files"):
        assert REGISTRY[name].confirmation_summary is None
    for name in ("run_command", "write_file"):
        assert REGISTRY[name].confirmation_summary is not None


# --- AC: exact schemas -------------------------------------------------------

def test_ac_run_command_schema_exact():
    # Amended by Tier 1 for the 2026-08-05 Qwen 3 (DashScope) streaming fix:
    # every tool schema gained "additionalProperties": False for stricter
    # provider-side validation.
    from sasha.tools import REGISTRY
    assert REGISTRY["run_command"].schema == {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Propose one bash command to run in the user's shell. "
                "The user sees the command and your description and "
                "must approve it before it runs."),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "exact bash command"},
                    "description": {
                        "type": "string",
                        "description": "one line: what this does and why"},
                },
                "required": ["command", "description"],
                "additionalProperties": False,
            },
        },
    }


def test_ac_write_file_schema_exact():
    from sasha.tools import REGISTRY
    schema = REGISTRY["write_file"].schema
    assert schema["function"]["name"] == "write_file"
    params = schema["function"]["parameters"]
    assert set(params["properties"]) == {"path", "content", "description"}
    assert params["required"] == ["path", "content", "description"]


def test_ac_read_file_schema_exact():
    from sasha.tools import REGISTRY
    schema = REGISTRY["read_file"].schema
    assert schema["function"]["name"] == "read_file"
    params = schema["function"]["parameters"]
    assert set(params["properties"]) == {"path"}
    assert params["required"] == ["path"]


def test_ac_list_dir_schema_exact():
    from sasha.tools import REGISTRY
    schema = REGISTRY["list_dir"].schema
    assert schema["function"]["name"] == "list_dir"
    params = schema["function"]["parameters"]
    assert set(params["properties"]) == {"path"}
    assert params["required"] == []


def test_ac_search_files_schema_exact():
    from sasha.tools import REGISTRY
    schema = REGISTRY["search_files"].schema
    assert schema["function"]["name"] == "search_files"
    params = schema["function"]["parameters"]
    assert set(params["properties"]) == {"pattern", "path"}
    assert params["required"] == ["pattern"]


# --- AC: confirmation_summary text -------------------------------------------

def test_ac_run_command_confirmation_summary_is_command_text():
    from sasha.tools import REGISTRY
    summary = REGISTRY["run_command"].confirmation_summary(
        {"command": "ls -la", "description": "list"})
    assert summary == "ls -la"


def test_ac_write_file_confirmation_summary_has_path_and_size():
    from sasha.tools import REGISTRY
    summary = REGISTRY["write_file"].confirmation_summary(
        {"path": "foo.txt", "content": "hello", "description": "d"})
    assert "foo.txt" in summary
    assert "5" in summary  # len("hello")


# --- AC: repetition_key -------------------------------------------------------

def test_ac_run_command_repetition_key_is_command_string():
    from sasha.tools import REGISTRY
    key_fn = REGISTRY["run_command"].repetition_key
    assert key_fn({"command": "ls", "description": "a"}) == "ls"
    assert key_fn({"command": "ls", "description": "b"}) == "ls"


def test_ac_write_file_repetition_key_excludes_description():
    from sasha.tools import REGISTRY
    key_fn = REGISTRY["write_file"].repetition_key
    k1 = key_fn({"path": "a.txt", "content": "x", "description": "one"})
    k2 = key_fn({"path": "a.txt", "content": "x", "description": "two"})
    assert k1 == k2 == ("a.txt", "x")
    k3 = key_fn({"path": "a.txt", "content": "y", "description": "one"})
    assert k3 != k1


def test_ac_read_only_repetition_key_is_full_args():
    from sasha.tools import REGISTRY
    for name in ("read_file", "list_dir", "search_files"):
        key_fn = REGISTRY[name].repetition_key
        args = {"path": "sub"}
        assert key_fn(args) == args


# --- AC: _safe_path ------------------------------------------------------------

def test_ac_safe_path_relative_inside_cwd(tmp_path):
    from sasha.tools import _safe_path
    result = _safe_path("sub/f.txt", str(tmp_path))
    assert result == (tmp_path / "sub" / "f.txt").resolve()


def test_ac_safe_path_dotdot_escapes_cwd(tmp_path):
    from sasha.tools import _safe_path
    assert _safe_path("../escape.txt", str(tmp_path)) is None


def test_ac_safe_path_absolute_inside_cwd(tmp_path):
    from sasha.tools import _safe_path
    target = tmp_path / "x.txt"
    assert _safe_path(str(target), str(tmp_path)) == target.resolve()


def test_ac_safe_path_absolute_outside_cwd(tmp_path):
    from sasha.tools import _safe_path
    assert _safe_path("/etc/passwd", str(tmp_path)) is None


def test_ac_safe_path_symlink_escapes_cwd(tmp_path):
    from sasha.tools import _safe_path
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    try:
        link = tmp_path / "link"
        os.symlink(outside, link)
        assert _safe_path("link", str(tmp_path)) is None
    finally:
        outside.rmdir()


# --- AC: read_file handler -----------------------------------------------------

def test_ac_read_file_success(tmp_path):
    from sasha.tools import REGISTRY
    (tmp_path / "f.txt").write_text("hello world")
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["read_file"].handler({"path": "f.txt"}, executor)
    assert result.text == "hello world"


def test_ac_read_file_not_found(tmp_path):
    from sasha.tools import REGISTRY
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["read_file"].handler({"path": "missing.txt"}, executor)
    assert result.text == "no such file: missing.txt"


def test_ac_read_file_path_escapes(tmp_path):
    from sasha.tools import REGISTRY
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["read_file"].handler({"path": "../escape.txt"}, executor)
    assert result.text == "path escapes the current working directory: ../escape.txt"


def test_ac_read_file_truncates_over_cap(tmp_path):
    from sasha.tools import REGISTRY
    big = "x" * 50000
    (tmp_path / "big.txt").write_text(big)
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["read_file"].handler({"path": "big.txt"}, executor)
    assert result.text.startswith("x" * 100)
    assert result.text.endswith("... [truncated at 40000 characters]")
    assert len(result.text) == 40000 + len("\n... [truncated at 40000 characters]")


# --- AC: list_dir handler -------------------------------------------------------

def test_ac_list_dir_success(tmp_path):
    from sasha.tools import REGISTRY
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "sub").mkdir()
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["list_dir"].handler({"path": "."}, executor)
    assert result.text == "a.txt\nb.txt\nsub/"


def test_ac_list_dir_defaults_to_current_directory(tmp_path):
    from sasha.tools import REGISTRY
    (tmp_path / "only.txt").write_text("")
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["list_dir"].handler({}, executor)
    assert result.text == "only.txt"


def test_ac_list_dir_not_found(tmp_path):
    from sasha.tools import REGISTRY
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["list_dir"].handler({"path": "nope"}, executor)
    assert result.text == "no such directory: nope"


def test_ac_list_dir_not_a_directory(tmp_path):
    from sasha.tools import REGISTRY
    (tmp_path / "f.txt").write_text("hi")
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["list_dir"].handler({"path": "f.txt"}, executor)
    assert result.text == "not a directory: f.txt"


def test_ac_list_dir_truncates_over_cap(tmp_path):
    from sasha.tools import REGISTRY
    for i in range(201):
        (tmp_path / f"f{i:04d}.txt").write_text("")
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["list_dir"].handler({"path": "."}, executor)
    lines = result.text.split("\n")
    assert len(lines) == 201
    assert lines[-1] == "... [1 more entries not shown]"


# --- AC: search_files handler ---------------------------------------------------

def test_ac_search_files_success(tmp_path):
    from sasha.tools import REGISTRY
    (tmp_path / "f.txt").write_text("alpha\nbeta needle\ngamma\n")
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["search_files"].handler(
        {"pattern": "needle", "path": "."}, executor)
    assert result.text == "f.txt:2: beta needle"


def test_ac_search_files_no_matches(tmp_path):
    from sasha.tools import REGISTRY
    (tmp_path / "f.txt").write_text("nothing here\n")
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["search_files"].handler(
        {"pattern": "needle", "path": "."}, executor)
    assert result.text == "no matches"


def test_ac_search_files_invalid_pattern(tmp_path):
    from sasha.tools import REGISTRY
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["search_files"].handler(
        {"pattern": "(unclosed", "path": "."}, executor)
    assert result.text.startswith("invalid pattern:")


def test_ac_search_files_path_escapes(tmp_path):
    from sasha.tools import REGISTRY
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["search_files"].handler(
        {"pattern": "x", "path": "../escape"}, executor)
    assert result.text == "path escapes the current working directory: ../escape"


def test_ac_search_files_skips_binary_files(tmp_path):
    from sasha.tools import REGISTRY
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00needle\x00")
    (tmp_path / "text.txt").write_text("needle here\n")
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["search_files"].handler(
        {"pattern": "needle", "path": "."}, executor)
    assert result.text == "text.txt:1: needle here"


def test_ac_search_files_truncates_over_cap(tmp_path):
    from sasha.tools import REGISTRY
    lines = "\n".join(f"needle{i}" for i in range(101))
    (tmp_path / "many.txt").write_text(lines)
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["search_files"].handler(
        {"pattern": "needle", "path": "."}, executor)
    out_lines = result.text.split("\n")
    assert len(out_lines) == 101
    assert out_lines[-1] == "... [1 more matches not shown]"


# --- AC: write_file handler ------------------------------------------------------

def test_ac_write_file_creates_parents_and_writes(tmp_path):
    from sasha.tools import REGISTRY
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["write_file"].handler(
        {"path": "a/b/c.txt", "content": "hello", "description": "d"}, executor)
    assert (tmp_path / "a" / "b" / "c.txt").read_text() == "hello"
    assert result.text == "wrote 5 bytes to a/b/c.txt"


def test_ac_write_file_overwrites_existing(tmp_path):
    from sasha.tools import REGISTRY
    (tmp_path / "f.txt").write_text("old")
    executor = FakeExecutor(str(tmp_path))
    REGISTRY["write_file"].handler(
        {"path": "f.txt", "content": "new", "description": "d"}, executor)
    assert (tmp_path / "f.txt").read_text() == "new"


def test_ac_write_file_not_sandboxed(tmp_path):
    from sasha.tools import REGISTRY
    outside_dir = tmp_path.parent / f"outside-write-{tmp_path.name}"
    outside_dir.mkdir()
    try:
        cwd_dir = tmp_path / "cwd"
        cwd_dir.mkdir()
        executor = FakeExecutor(str(cwd_dir))
        target = outside_dir / "escaped.txt"
        result = REGISTRY["write_file"].handler(
            {"path": str(target), "content": "z", "description": "d"}, executor)
        assert target.read_text() == "z"
        assert "wrote 1 bytes to" in result.text
    finally:
        for child in outside_dir.iterdir():
            child.unlink()
        outside_dir.rmdir()


def test_ac_write_file_oserror_reported_not_raised(tmp_path):
    from sasha.tools import REGISTRY
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    executor = FakeExecutor(str(tmp_path))
    result = REGISTRY["write_file"].handler(
        {"path": "blocker/sub/file.txt", "content": "x", "description": "d"},
        executor)
    assert result.text.startswith("error writing blocker/sub/file.txt:")


# --- AC: run_command handler (pure refactor, no behavior change) -----------------

def test_ac_run_command_handler_formats_result():
    from sasha.tools import REGISTRY
    executor = FakeExecutor("/spy")
    result = REGISTRY["run_command"].handler(
        {"command": "ls -la", "description": "d"}, executor)
    assert executor.calls == [("ls -la", True)]
    assert result.text == "exit code: 0\nstdout:\nout\nstderr:\nerr"
