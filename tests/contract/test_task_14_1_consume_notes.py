"""Contract tests — Task 14.1: build_messages() clears notes/last_stderr
after use (ISSUE-031).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-14.1-consume-notes.md
"""


def _result(exit_code, stderr=""):
    from sasha.executor import ExecResult

    return ExecResult(exit_code=exit_code, signal=None, stderr_text=stderr,
                      stdout_text="", cwd_after="/", duration_ms=1)


# --- AC1: notes don't survive a second build_messages() call ---------------

def test_ac1_notes_cleared_after_one_build():
    from sasha.session import Session

    s = Session()
    s.note_command("hi there", _result(127))
    s.add_user("first question")
    first = s.build_messages("/somewhere")
    assert "hi there" in first[-1]["content"]
    assert "127" in first[-1]["content"]

    s.add_user("second, unrelated question")
    second = s.build_messages("/somewhere")
    assert "hi there" not in second[-1]["content"]
    assert "127" not in second[-1]["content"]


# --- AC2: last_stderr doesn't survive a second build_messages() call -------

def test_ac2_last_stderr_cleared_after_one_build():
    from sasha.session import Session

    s = Session()
    s.note_command("hi there", _result(127, "bash: hi: command not found"))
    s.add_user("first question")
    first = s.build_messages("/somewhere")
    assert "bash: hi: command not found" in first[-1]["content"]

    s.add_user("second, unrelated question")
    second = s.build_messages("/somewhere")
    assert "bash: hi: command not found" not in second[-1]["content"]


# --- AC3: the first build still shows notes correctly (regression guard) ---

def test_ac3_first_build_still_includes_notes():
    # Amended by Tier 1 for Task 32.1 (ISSUE-050): cwd is no longer part
    # of the notes-carrying user message -- it arrives as its own
    # preceding [context:cwd] marker message instead. See
    # docs/superpowers/specs/2026-07-26-issue-050-context-injection-syntax-design.md.
    from sasha.session import Session

    s = Session()
    s.note_command("ls -la", _result(0))
    s.note_command("false", _result(1))
    s.add_user("why did the second one fail?")

    msgs = s.build_messages("/project")
    content = msgs[-1]["content"]
    assert "[context:cwd] /project" in msgs[-2]["content"]
    assert "ls -la" in content
    assert "false" in content
    assert "why did the second one fail?" in content


# --- AC4: multiple accumulated notes clear together, not partially ---------

def test_ac4_multiple_accumulated_notes_clear_together():
    from sasha.session import Session

    s = Session()
    s.note_command("cmd-a", _result(0))
    s.note_command("cmd-b", _result(1))
    s.note_command("cmd-c", _result(2))
    s.add_user("question about all three")
    first = s.build_messages("/w")
    assert "cmd-a" in first[-1]["content"]
    assert "cmd-b" in first[-1]["content"]
    assert "cmd-c" in first[-1]["content"]

    s.add_user("follow-up")
    second = s.build_messages("/w")
    content = second[-1]["content"]
    assert "cmd-a" not in content
    assert "cmd-b" not in content
    assert "cmd-c" not in content


# --- AC5: a new note_command after a clear repopulates cleanly -------------

def test_ac5_new_note_after_clear_shows_only_the_new_one():
    from sasha.session import Session

    s = Session()
    s.note_command("old-command", _result(127))
    s.add_user("about the old one")
    s.build_messages("/w")  # consumes "old-command"

    s.note_command("new-command", _result(2))
    s.add_user("about the new one")
    msgs = s.build_messages("/w")
    content = msgs[-1]["content"]
    assert "new-command" in content
    assert "old-command" not in content
