"""Regression tests for the string/bytes handling fixes (ISSUE-072).

Each test corresponds to a finding in docs/analysis/string-handling-findings.md and
was written by reproducing the original defect first. The reproductions that
matter are the ones where the *content* is reachable from ordinary model output
(a JSON lone-surrogate escape) or from the filesystem (a path whose bytes are
not valid UTF-8) -- not only from an exotic locale.
"""

import json
import os
import stat
import subprocess


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True)


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


class _Exec:
    """Minimal stand-in for the executor: only `cwd` is read by the handlers."""

    def __init__(self, cwd):
        self.cwd = str(cwd)
        self.sensitive_path_patterns = ()


def _lone_surrogate():
    """Exactly what a model can send: JSON permits a lone \\udXXX escape."""
    return json.loads('"\\ud800"')


# ==========================================================================
# ISSUE-072 S1 — write_file must never destroy the target file
# ==========================================================================

def test_s1_write_file_surrogate_does_not_destroy_existing_file(tmp_path):
    from sasha.tools import _write_file_handler

    target = tmp_path / "keep.txt"
    original = "ORIGINAL CONTENT\n"
    target.write_text(original)

    result = _write_file_handler(
        {"path": "keep.txt", "content": _lone_surrogate()}, _Exec(tmp_path))

    assert "error writing" in result.text
    assert target.read_text() == original          # <-- the whole point


def test_s1_write_file_failure_leaves_no_temp_debris(tmp_path):
    from sasha.tools import _write_file_handler

    (tmp_path / "keep.txt").write_text("x")
    _write_file_handler(
        {"path": "keep.txt", "content": _lone_surrogate()}, _Exec(tmp_path))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]


def test_s1_write_file_preserves_mode_when_overwriting(tmp_path):
    from sasha.tools import _write_file_handler

    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\necho old\n")
    script.chmod(0o755)

    _write_file_handler({"path": "run.sh", "content": "#!/bin/sh\necho new\n"},
                        _Exec(tmp_path))

    assert stat.S_IMODE(script.stat().st_mode) == 0o755
    assert "new" in script.read_text()


def test_s1_write_file_reports_bytes_not_characters(tmp_path):
    from sasha.tools import _write_file_handler

    result = _write_file_handler({"path": "a.txt", "content": "caf\u00e9"},
                                 _Exec(tmp_path))

    assert result.text == "wrote 5 bytes to a.txt"    # not 4 characters
    # ...and the confirmation prompt the user approves says the same thing
    from sasha.tools import REGISTRY
    summary = REGISTRY["write_file"].confirmation_summary(
        {"path": "a.txt", "content": "caf\u00e9"})
    assert "(5 bytes)" in summary


def test_s1_write_file_creates_new_file_normally(tmp_path):
    from sasha.tools import _write_file_handler

    result = _write_file_handler({"path": "new.txt", "content": "hello"},
                                 _Exec(tmp_path))

    assert "wrote 5 bytes" in result.text
    assert (tmp_path / "new.txt").read_text() == "hello"


# ==========================================================================
# ISSUE-072 S2/S3 — a file we did not write must not stop startup
# ==========================================================================

def _write_latin1_memory(memory_root):
    memory_root.mkdir(parents=True, exist_ok=True)
    (memory_root / "semantic.jsonl").write_bytes(
        b'{"timestamp":"2026-01-01T00:00:00Z","kind":"remember",'
        b'"text":"caf\xe9","pointer":null}\n'
        b'{"timestamp":"2026-01-01T00:00:01Z","kind":"remember",'
        b'"text":"second entry","pointer":null}\n')


def test_s2_undecodable_project_memory_does_not_break_place_shift(tmp_path):
    from sasha.repl import Repl
    from sasha.session import Session
    from sasha.config import Config, Limits, Profile
    from sasha.executor import Executor

    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_latin1_memory(repo / ".sasha" / "memory")

    class Sink:
        color_enabled = False
        lines = []

        def write_line(self, text, color=None):
            self.lines.append(text)

        def write_chunk(self, text):
            pass

        def ensure_newline(self):
            pass

    config = Config(default_profile="p",
                    profiles={"p": Profile("p", "http://x/v1", "m", "", False)},
                    limits=Limits())
    repl = Repl(config, Executor(str(repo), {}), Session(), lambda p: None,
                None, lambda prompt: "", Sink())

    repl._check_place_shift()          # must not raise


def test_s2_undecodable_general_memory_does_not_break_startup(tmp_path, monkeypatch):
    from sasha import memory

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    _write_latin1_memory(memory.general_memory_root())

    block, count = memory.load_general_memory_block()   # must not raise

    assert count == 2                       # the good entry is not lost


def test_s3_undecodable_skill_file_does_not_break_startup(tmp_path, monkeypatch):
    from sasha import skills

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    root = (tmp_path / "cfg" / "sasha" / "skills" / "mkt" / "plug" / "1.0.0"
            / "skills")
    (root / "bad").mkdir(parents=True)
    (root / "bad" / "SKILL.md").write_bytes(
        b"---\nname: bad\ndescription: caf\xe9 latin-1\n---\nbody\n")
    (root / "good").mkdir()
    (root / "good" / "SKILL.md").write_text(
        "---\nname: good\ndescription: fine\n---\nbody\n")

    listing = skills.build_skills_listing()             # must not raise

    assert "good" in listing


# ==========================================================================
# ISSUE-072 S4 — reading a binary file is an answer, not a crash
# ==========================================================================

def test_s4_read_file_on_binary_returns_text_with_a_notice(tmp_path):
    from sasha.tools import _read_file_handler

    (tmp_path / "blob.pyc").write_bytes(bytes(range(256)))

    result = _read_file_handler({"path": "blob.pyc"}, _Exec(tmp_path))

    assert "not valid UTF-8" in result.text
    assert "\ufffd" in result.text


def test_s4_read_file_on_utf8_file_has_no_notice(tmp_path):
    from sasha.tools import _read_file_handler

    (tmp_path / "a.txt").write_text("caf\u00e9 \u2014 fine")

    result = _read_file_handler({"path": "a.txt"}, _Exec(tmp_path))

    assert "not valid UTF-8" not in result.text
    assert "caf\u00e9" in result.text


# ==========================================================================
# ISSUE-072 S5 — a working directory whose name is not valid UTF-8
# ==========================================================================

def test_s5_executor_tolerates_a_non_utf8_cwd(tmp_path):
    """`pwd -P` returns raw bytes; decoding them strictly used to raise inside
    the result-building path, losing the command's result and never updating
    the cwd -- so every later command in that directory failed too."""
    from sasha.executor import Executor

    raw = b"caf\xe9_dir"
    os.mkdir(os.path.join(os.fsencode(tmp_path), raw))
    weird = os.path.join(str(tmp_path), "caf\udce9_dir")   # surrogateescape form

    executor = Executor(weird, {})
    result = executor.run("true")

    assert result.exit_code == 0


def test_s5_side_channel_decodes_surrogateescape():
    """The decode choice itself, without needing a PTY."""
    raw = b"/tmp/caf\xe9_dir"
    decoded = raw.decode("utf-8", "surrogateescape")

    assert decoded == "/tmp/caf\udce9_dir"
    assert decoded.encode("utf-8", "surrogateescape") == raw   # round-trips


# ==========================================================================
# ISSUE-072 S6/S7 — surrogates must not reach a strict stdout
# ==========================================================================

def test_s6_printable_replaces_surrogates_only():
    from sasha.textio import printable

    assert printable("plain text") == "plain text"     # untouched
    assert printable("") == ""
    assert printable("caf\udce9") == "caf\ufffd"
    assert printable("caf\u00e9") == "caf\u00e9"       # real UTF-8 is preserved


def test_s6_prompt_render_survives_a_surrogate_cwd():
    from sasha import prompt

    rendered = prompt.render(prompt.DEFAULT_TEMPLATE, user="u", host="h",
                             cwd="/tmp/caf\udce9_dir", home="/home/u",
                             color_enabled=False)

    rendered.encode("utf-8")          # would raise before the fix
    assert "\ufffd" in rendered


def test_s7_sink_write_line_survives_a_surrogate(capsys):
    """pytest's capture stream is UTF-8/strict, exactly like a real terminal,
    so writing a surrogate here is fatal without the fix."""
    from sasha.repl import TerminalSink

    TerminalSink().write_line("caf\udce9")            # must not raise

    assert "\ufffd" in capsys.readouterr().out


def test_s7_sink_write_chunk_survives_a_surrogate_from_model_output(capsys):
    """A lone surrogate arrives via json.loads of an SSE payload; writing it
    used to abort the whole consult."""
    from sasha.repl import TerminalSink

    TerminalSink().write_chunk(_lone_surrogate())     # must not raise

    assert "\ufffd" in capsys.readouterr().out


# ==========================================================================
# ISSUE-072 S8 — compaction must not rewrite a log it cannot decode
# ==========================================================================

def test_s8_compaction_refuses_to_mangle_an_undecodable_log(tmp_path, monkeypatch):
    from sasha import tool_log

    log = tmp_path / "tool-calls.jsonl"
    raw = (b'{"timestamp":"2026-01-01T00:00:00Z","command":"caf\xe9",'
           b'"result":"x"}\n')
    log.write_bytes(raw)
    monkeypatch.setattr(tool_log, "_log_path", lambda: log)

    result = tool_log.compact(max_size_mb=0)

    assert result.after_bytes == result.before_bytes   # nothing rewritten
    assert result.still_too_big is False
    assert log.read_bytes() == raw            # byte-identical, not mangled


def test_s6_printable_handles_a_lone_json_surrogate():
    """U+DC80-U+DCFF (surrogateescape) and U+D800-D7FF (a JSON \\udXXX escape)
    are different animals; both must become exactly ONE U+FFFD."""
    from sasha.textio import printable

    assert printable("caf\udce9") == "caf\ufffd"      # surrogateescape form
    assert printable("caf\ud800") == "caf\ufffd"      # lone JSON surrogate
    assert printable("\ud800\ud800") == "\ufffd\ufffd"
