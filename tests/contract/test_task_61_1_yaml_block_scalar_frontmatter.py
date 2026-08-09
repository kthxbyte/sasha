"""Contract tests — Task 61.1: YAML block-scalar frontmatter parsing.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-61.1-yaml-block-scalar-frontmatter.md

AC1 uses an excerpt of the real, as-installed ponytail SKILL.md
(https://github.com/DietrichGebert/ponytail) that exposed the bug --
folded (`>`) description with colon-containing continuation lines and a
trailing single-line key after the block.
"""

_PONYTAIL_EXCERPT = (
    "---\n"
    "name: ponytail\n"
    "description: >\n"
    "  Forces the laziest solution that actually works, simplest, shortest, most\n"
    "  minimal. Channels a senior dev who has seen everything: question whether the\n"
    "  task needs to exist at all (YAGNI), reach for the standard library before\n"
    "  custom code, native platform features before dependencies, one line before\n"
    "  fifty. Supports intensity levels: lite, full (default), ultra. Use on ANY\n"
    "  coding task: writing, adding, refactoring, fixing, reviewing, or designing\n"
    "  code, and choosing libraries or dependencies.\n"
    "argument-hint: \"[lite|full|ultra]\"\n"
    "license: MIT\n"
    "---\n"
    "\n"
    "# Ponytail\n"
)

_PONYTAIL_EXPECTED_DESCRIPTION = (
    "Forces the laziest solution that actually works, simplest, shortest, most "
    "minimal. Channels a senior dev who has seen everything: question whether the "
    "task needs to exist at all (YAGNI), reach for the standard library before "
    "custom code, native platform features before dependencies, one line before "
    "fifty. Supports intensity levels: lite, full (default), ultra. Use on ANY "
    "coding task: writing, adding, refactoring, fixing, reviewing, or designing "
    "code, and choosing libraries or dependencies."
)


# --- AC1: real ponytail excerpt, folded scalar with colons in the body -------

def test_ac1_folded_scalar_matches_real_ponytail_skill():
    from sasha.skills import _parse_frontmatter

    result = _parse_frontmatter(_PONYTAIL_EXCERPT)
    assert result == {
        "name": "ponytail",
        "description": _PONYTAIL_EXPECTED_DESCRIPTION,
        "argument-hint": '"[lite|full|ultra]"',
        "license": "MIT",
    }


# --- AC2: folded scalar joins lines with spaces, no trailing key pollution ---

def test_ac2_folded_scalar_basic():
    from sasha.skills import _parse_frontmatter

    text = (
        "---\n"
        "name: x\n"
        "description: >\n"
        "  line one\n"
        "  line two\n"
        "---\n"
    )
    assert _parse_frontmatter(text) == {"name": "x", "description": "line one line two"}


# --- AC3: folded scalar blank line becomes a paragraph break -----------------

def test_ac3_folded_scalar_blank_line_is_paragraph_break():
    from sasha.skills import _parse_frontmatter

    text = (
        "---\n"
        "name: x\n"
        "description: >\n"
        "  first paragraph line one\n"
        "  first paragraph line two\n"
        "\n"
        "  second paragraph\n"
        "---\n"
    )
    result = _parse_frontmatter(text)
    assert result["description"] == (
        "first paragraph line one first paragraph line two\n"
        "second paragraph")


# --- AC4: literal scalar preserves line breaks --------------------------------

def test_ac4_literal_scalar_preserves_newlines():
    from sasha.skills import _parse_frontmatter

    text = (
        "---\n"
        "name: x\n"
        "body: |\n"
        "  line one\n"
        "  line two\n"
        "---\n"
    )
    assert _parse_frontmatter(text) == {"name": "x", "body": "line one\nline two"}


# --- AC5: chomping-indicator variants (>-, >+, |-, |+) still trigger block ---

def test_ac5_chomping_indicator_variants():
    from sasha.skills import _parse_frontmatter

    for indicator in (">-", ">+", "|-", "|+"):
        text = (
            "---\n"
            "name: x\n"
            f"description: {indicator}\n"
            "  hello\n"
            "---\n"
        )
        result = _parse_frontmatter(text)
        assert result["description"] == "hello", f"indicator {indicator} failed"


# --- AC6: a plain single-line value is unaffected (regression) --------------

def test_ac6_plain_single_line_value_unaffected():
    from sasha.skills import _parse_frontmatter

    text = "---\nname: brainstorming\ndescription: Explore first.\n---\n"
    assert _parse_frontmatter(text) == {
        "name": "brainstorming", "description": "Explore first."}


# --- AC7: build_skills_listing() shows the real description, not '>' --------

def test_ac7_listing_uses_real_description_not_indicator(monkeypatch, tmp_path):
    from sasha import skills

    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    skill_path = (xdg / "sasha" / "skills" / "mkt" / "ponytail" / "4.9.0"
                  / "skills" / "ponytail" / "SKILL.md")
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(_PONYTAIL_EXCERPT)

    listing = skills.build_skills_listing()
    assert ": >" not in listing
    assert _PONYTAIL_EXPECTED_DESCRIPTION in listing
