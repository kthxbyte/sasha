"""Contract tests — Task 56.1: directory-recap cold-start fallback
(marker-file content-sniff) (ISSUE-056).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-56.1-recap-cold-start-fallback.md
Design: docs/superpowers/specs/2026-07-28-issue-056-recap-cold-start-design.md
"""

from __future__ import annotations

import subprocess


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                    capture_output=True)


def _init_repo(repo):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


# --- AC1/AC2: marker-file gate, baseline behavior ---------------------------

def test_ac1_marker_file_escalates_with_no_history(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "coldstart"
    target.mkdir()
    (target / "README.md").write_text("This is the coldstart project.\n")

    verdict, text = directory_recap.decide(target, log_entries=[])
    assert verdict == directory_recap.ESCALATE
    assert text is None


def test_ac2_no_marker_no_history_stays_silent(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "plain_folder"
    target.mkdir()
    (target / "notes.txt").write_text("just some notes")

    verdict, text = directory_recap.decide(target, log_entries=[])
    assert verdict == directory_recap.SILENT
    assert text is None


# --- AC3/AC4: vendor/cache exclusion overrides the marker match -------------

def test_ac3_dot_prefixed_ancestor_stays_silent_despite_marker(tmp_path):
    from sasha import directory_recap

    target = tmp_path / ".cache" / "some-app"
    target.mkdir(parents=True)
    (target / "README.md").write_text("cache internals\n")

    verdict, text = directory_recap.decide(target, log_entries=[])
    assert verdict == directory_recap.SILENT
    assert text is None


def test_ac4_vendor_dir_ancestor_stays_silent_despite_marker(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "node_modules" / "some-pkg"
    target.mkdir(parents=True)
    (target / "package.json").write_text("{}")
    (target / "LICENSE").write_text("MIT\n")

    verdict, text = directory_recap.decide(target, log_entries=[])
    assert verdict == directory_recap.SILENT
    assert text is None


# --- AC5: pre-existing gap fix — git repo, no subtree, now escalates --------

def test_ac5_git_repo_with_marker_and_no_subtree_escalates(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "myrepo"
    _init_repo(target)
    (target / "README.md").write_text("myrepo\n")

    verdict, text = directory_recap.decide(target, log_entries=[])
    assert verdict == directory_recap.ESCALATE
    assert text is None


# --- AC6: source-extension-count rule is intentionally absent ---------------

def test_ac6_source_extensions_alone_stay_silent(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "scripts_only"
    target.mkdir()
    (target / "a.py").write_text("print(1)\n")
    (target / "b.py").write_text("print(2)\n")
    (target / "c.js").write_text("console.log(1)\n")

    verdict, text = directory_recap.decide(target, log_entries=[])
    assert verdict == directory_recap.SILENT
    assert text is None


# --- AC7: gather_context() cold-start content-sniff -------------------------

def test_ac7_gather_context_cold_start_readme_and_listing(tmp_path):
    from sasha import directory_recap

    with_readme = tmp_path / "with_readme"
    with_readme.mkdir()
    (with_readme / "README.md").write_text("A" * 600)
    (with_readme / "main.py").write_text("pass\n")
    (with_readme / ".hidden").write_text("nope")

    ctx = directory_recap.gather_context(with_readme, log_entries=[])
    assert ctx["self_commit"] is None
    assert ctx["child_commits"] == []
    assert ctx["recent_commands"] == []
    assert ctx["readme_excerpt"] is not None
    assert len(ctx["readme_excerpt"]) == directory_recap.README_EXCERPT_CHARS
    assert "main.py" in ctx["top_level_listing"]
    assert ".hidden" not in ctx["top_level_listing"]

    without_readme = tmp_path / "without_readme"
    without_readme.mkdir()

    ctx2 = directory_recap.gather_context(without_readme, log_entries=[])
    assert ctx2["readme_excerpt"] is None
    assert ctx2["top_level_listing"] == []


# --- AC8: build_prompt()'s new conditional section --------------------------

def test_ac8_build_prompt_includes_directory_contents_only_when_present():
    from sasha import directory_recap

    cold_ctx = {
        "target_name": "coldstart",
        "self_commit": None,
        "child_commits": [],
        "recent_commands": [],
        "readme_excerpt": "This project does X.",
        "top_level_listing": ["main.py", "README.md"],
    }
    prompt = directory_recap.build_prompt(cold_ctx)
    assert "Directory contents:" in prompt
    assert "This project does X." in prompt
    assert "main.py" in prompt

    warm_ctx = {
        "target_name": "warm",
        "self_commit": "2 hours ago\tdid a thing",
        "child_commits": [],
        "recent_commands": [],
    }
    prompt2 = directory_recap.build_prompt(warm_ctx)
    assert "Directory contents:" not in prompt2


# --- AC9: explicit sasha recap's content-sniff ignores the vendor gate -----

def test_ac9_gather_context_ignores_vendor_gate(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "node_modules" / "some-pkg"
    target.mkdir(parents=True)
    (target / "README.md").write_text("some-pkg does Y.\n")

    ctx = directory_recap.gather_context(target, log_entries=[])
    assert ctx["readme_excerpt"] is not None
    assert "some-pkg does Y." in ctx["readme_excerpt"]
