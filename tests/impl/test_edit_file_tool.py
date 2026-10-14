"""Tests for the `edit_file` tool (ISSUE-076).

Why the tool exists: `write_file` can only replace a whole file, so changing one
line of a large one meant regenerating all of it — minutes of generation on a
26B, and impossible past `read_file`'s 40000-character cap, because the model
could never see the whole file in order to reproduce it.
"""

import stat



class _Exec:
    def __init__(self, cwd):
        self.cwd = str(cwd)
        self.sensitive_path_patterns = ()


def _edit(tmp_path, **args):
    from sasha.tools import _edit_file_handler
    return _edit_file_handler(args, _Exec(tmp_path))


# ==========================================================================
# The happy path
# ==========================================================================

def test_replaces_one_region_and_leaves_the_rest_alone(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("import os\n\n\ndef f():\n    return 1\n\n\ndef g():\n    return 2\n")

    r = _edit(tmp_path, path="m.py", old_string="    return 1",
              new_string="    return 42")

    assert "edited m.py" in r.text
    assert f.read_text() == (
        "import os\n\n\ndef f():\n    return 42\n\n\ndef g():\n    return 2\n")


def test_empty_new_string_deletes(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("keep\nremove me\nkeep too\n")

    _edit(tmp_path, path="m.py", old_string="remove me\n", new_string="")

    assert f.read_text() == "keep\nkeep too\n"


def test_multiline_replacement(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def f():\n    a = 1\n    b = 2\n")

    _edit(tmp_path, path="m.py", old_string="    a = 1\n    b = 2\n",
          new_string="    return 3\n")

    assert f.read_text() == "def f():\n    return 3\n"


def test_reports_the_line_delta(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("a\nb\nc\n")

    r = _edit(tmp_path, path="m.py", old_string="b\n", new_string="x\ny\n")

    assert "1 line(s)" in r.text and "2" in r.text


# ==========================================================================
# Refusals — nothing may be written when the edit is ambiguous
# ==========================================================================

def test_missing_old_string_is_refused_and_changes_nothing(tmp_path):
    f = tmp_path / "m.py"
    original = "def f():\n    return 1\n"
    f.write_text(original)

    r = _edit(tmp_path, path="m.py", old_string="    return 999",
              new_string="    return 2")

    assert "not found" in r.text
    assert f.read_text() == original


def test_ambiguous_old_string_is_refused_and_changes_nothing(tmp_path):
    """The uniqueness requirement: a bare 'return 1' in two functions must not
    be guessed at."""
    f = tmp_path / "m.py"
    original = "def f():\n    return 1\n\n\ndef g():\n    return 1\n"
    f.write_text(original)

    r = _edit(tmp_path, path="m.py", old_string="    return 1",
              new_string="    return 2")

    assert "appears 2 times" in r.text
    assert "nothing was changed" in r.text
    assert f.read_text() == original


def test_context_disambiguates_an_otherwise_ambiguous_match(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def f():\n    return 1\n\n\ndef g():\n    return 1\n")

    _edit(tmp_path, path="m.py", old_string="def g():\n    return 1",
          new_string="def g():\n    return 2")

    assert f.read_text() == "def f():\n    return 1\n\n\ndef g():\n    return 2\n"


def test_empty_old_string_is_refused(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x\n")

    r = _edit(tmp_path, path="m.py", old_string="", new_string="y")

    assert "must not be empty" in r.text
    assert f.read_text() == "x\n"


def test_missing_file_is_reported_not_created(tmp_path):
    r = _edit(tmp_path, path="nope.py", old_string="a", new_string="b")

    assert "no such file" in r.text
    assert not (tmp_path / "nope.py").exists()


def test_undecodable_file_is_refused(tmp_path):
    f = tmp_path / "blob.bin"
    raw = bytes(range(256))
    f.write_bytes(raw)

    r = _edit(tmp_path, path="blob.bin", old_string="a", new_string="b")

    assert "not valid UTF-8" in r.text
    assert f.read_bytes() == raw


def test_replacement_that_cannot_be_encoded_is_refused(tmp_path):
    """A lone surrogate can arrive from model output via a JSON escape."""
    import json
    f = tmp_path / "m.py"
    original = "x\n"
    f.write_text(original)

    r = _edit(tmp_path, path="m.py", old_string="x",
              new_string=json.loads('"\\ud800"'))

    assert "not encodable" in r.text
    assert f.read_text() == original


# ==========================================================================
# Byte-faithfulness outside the edited region
# ==========================================================================

def test_crlf_line_endings_are_preserved(tmp_path):
    """read_text()/write_text() translate newlines, so a naive edit rewrites
    EVERY line ending in the file while touching only one region."""
    f = tmp_path / "win.txt"
    raw = b"alpha\r\nbeta\r\ngamma\r\n"
    f.write_bytes(raw)

    _edit(tmp_path, path="win.txt", old_string="beta", new_string="BETA")

    assert f.read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


def test_a_file_without_a_trailing_newline_keeps_it_that_way(tmp_path):
    f = tmp_path / "m.py"
    f.write_bytes(b"a\nb")

    _edit(tmp_path, path="m.py", old_string="b", new_string="c")

    assert f.read_bytes() == b"a\nc"


def test_file_mode_is_preserved(tmp_path):
    f = tmp_path / "run.sh"
    f.write_text("#!/bin/sh\necho old\n")
    f.chmod(0o755)

    _edit(tmp_path, path="run.sh", old_string="echo old", new_string="echo new")

    assert stat.S_IMODE(f.stat().st_mode) == 0o755


def test_unicode_content_round_trips(tmp_path):
    f = tmp_path / "n.md"
    f.write_text("caf\u00e9 \u2014 \u00f1\n", encoding="utf-8")

    _edit(tmp_path, path="n.md", old_string="caf\u00e9", new_string="th\u00e9")

    assert f.read_text(encoding="utf-8") == "th\u00e9 \u2014 \u00f1\n"


# ==========================================================================
# Registration
# ==========================================================================

def test_registered_as_a_confirming_tool_not_an_auto_running_one():
    from sasha.tools import REGISTRY

    tool = REGISTRY["edit_file"]
    assert tool.requires_confirmation is True
    assert tool.repetition_key(
        {"path": "a", "old_string": "x", "new_string": "y"}) == ("a", "x", "y")


def test_confirmation_prompt_shows_the_change():
    from sasha.tools import REGISTRY

    summary = REGISTRY["edit_file"].confirmation_summary(
        {"path": "src/x.py", "old_string": "    return 1\n",
         "new_string": "    return 2\n"})

    assert "src/x.py" in summary
    assert "return 1" in summary and "-" in summary
    assert "return 2" in summary and "+" in summary


# ==========================================================================
# Tolerant matching (ISSUE-079) — Aider-shaped tiers
# ==========================================================================

def test_single_line_drift_matches_on_whitespace(tmp_path):
    """The commonest real drift: the model's indentation is close but not exact."""
    f = tmp_path / "m.py"
    f.write_text("def f():\n        return 1\n")

    r = _edit(tmp_path, path="m.py", old_string="    return 1",
              new_string="        return 42")

    assert "whitespace-insensitive" in r.text
    assert f.read_text() == "def f():\n        return 42\n"


def test_trailing_whitespace_drift_matches(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("alpha   \nbeta\n")

    r = _edit(tmp_path, path="m.py", old_string="alpha\nbeta\n",
              new_string="ALPHA\nBETA\n")

    assert "whitespace-insensitive" in r.text
    assert f.read_text() == "ALPHA\nBETA\n"


def test_whitespace_tier_refuses_when_it_would_be_ambiguous(tmp_path):
    """Tolerance must never mean guessing."""
    f = tmp_path / "m.py"
    original = "def f():\n    return 1\n\n\ndef g():\n    return 1\n"
    f.write_text(original)

    r = _edit(tmp_path, path="m.py", old_string="return 1",
              new_string="return 2")

    assert "appears" in r.text or "more than one place" in r.text
    assert f.read_text() == original


def test_whitespace_tier_keeps_a_crlf_file_crlf(tmp_path):
    """A replacement typed with LF must not leave one region of a CRLF file
    with different endings from the rest."""
    f = tmp_path / "win.txt"
    f.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")

    _edit(tmp_path, path="win.txt", old_string="beta\ngamma",
          new_string="BETA\nGAMMA")

    assert f.read_bytes() == b"alpha\r\nBETA\r\nGAMMA\r\n"


def test_elision_marker_replaces_a_span_the_model_did_not_quote(tmp_path):
    """Quote the ends of a long block instead of all of it."""
    f = tmp_path / "m.py"
    f.write_text("def f():\n    a = 1\n    b = 2\n    c = 3\n    return a\n")

    r = _edit(tmp_path, path="m.py",
              old_string="def f():\n...\n    return a\n",
              new_string="def f():\n    return 99\n")

    assert "elided" in r.text
    assert f.read_text() == "def f():\n    return 99\n"


def test_elision_refuses_when_the_head_is_ambiguous(tmp_path):
    f = tmp_path / "m.py"
    original = "def f():\n    return 1\n\n\ndef f():\n    return 2\n"
    f.write_text(original)

    r = _edit(tmp_path, path="m.py",
              old_string="def f():\n...\n    return 9\n",
              new_string="X\n")

    assert "not found" in r.text or "more than one place" in r.text
    assert f.read_text() == original


def test_not_found_offers_the_closest_real_line(tmp_path):
    """A bare "not found" costs a turn to recover from; a pointer does not."""
    f = tmp_path / "m.py"
    f.write_text("def compute(values):\n    total = sum(values)\n"
                 "    return total\n")

    r = _edit(tmp_path, path="m.py", old_string="return totaal",
              new_string="return 0")

    assert "not found" in r.text
    assert "closest line" in r.text
    assert "line 3" in r.text
    assert "return total" in r.text


def test_exact_still_wins_and_reports_which_tier_was_used(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("alpha\nbeta\n")

    r = _edit(tmp_path, path="m.py", old_string="beta", new_string="BETA")

    assert "(exact)" in r.text


def test_partial_line_exact_match_still_works(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("    return 1\n")

    _edit(tmp_path, path="m.py", old_string="return 1", new_string="return 2")

    assert f.read_text() == "    return 2\n"
