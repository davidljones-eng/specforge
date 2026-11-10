import re

import pytest

from conftest import headings, parse_frontmatter, section

AC_ID = re.compile(r"\bAC-\d+\b")


@pytest.fixture(scope="module")
def skill_dir(root):
    return root / "skills" / "spec-writing"


@pytest.fixture(scope="module")
def templates(skill_dir):
    return skill_dir / "templates"


def read(path):
    assert path.is_file(), f"missing {path}"
    return path.read_text()


# ---------- SKILL.md ----------

def test_skill_has_valid_frontmatter(skill_dir):
    meta, body = parse_frontmatter(read(skill_dir / "SKILL.md"))
    assert meta.get("name") == "spec-writing"
    assert len(meta.get("description", "")) > 20
    assert body.strip()


def test_skill_references_every_template(skill_dir):
    body = read(skill_dir / "SKILL.md")
    for name in ("requirement.md", "spec.md", "plan.md", "review.md", "CLAUDE.md"):
        assert name in body, f"SKILL.md should mention {name}"


def test_skill_explains_ac_ids(skill_dir):
    body = read(skill_dir / "SKILL.md")
    assert "AC-1" in body


# ---------- spec.md ----------

SPEC_SECTIONS = ["goal", "scope", "out of scope", "acceptance criteria", "edge cases"]


@pytest.mark.parametrize("title", SPEC_SECTIONS)
def test_spec_template_has_section(templates, title):
    assert title in headings(read(templates / "spec.md"))


def test_spec_template_has_numbered_ac_ids(templates):
    ac = section(read(templates / "spec.md"), "acceptance criteria")
    ids = AC_ID.findall(ac)
    assert "AC-1" in ids and "AC-2" in ids


def test_spec_template_edge_cases_not_empty(templates):
    assert section(read(templates / "spec.md"), "edge cases")


# ---------- requirement.md ----------

@pytest.mark.parametrize(
    "title", ["summary", "problem", "users", "requirements", "constraints", "open questions"]
)
def test_requirement_template_has_section(templates, title):
    assert title in headings(read(templates / "requirement.md"))


# ---------- plan.md ----------

@pytest.mark.parametrize("title", ["milestones", "tasks", "folder structure", "traceability"])
def test_plan_template_has_section(templates, title):
    assert title in headings(read(templates / "plan.md"))


def test_plan_template_tasks_map_to_ac_ids(templates):
    tasks = section(read(templates / "plan.md"), "tasks")
    task_lines = [l for l in tasks.splitlines() if re.match(r"\s*(- \[ \]|\d+\.|\|)", l)]
    assert task_lines
    assert all(AC_ID.search(l) for l in task_lines if "---" not in l and "Task" not in l)


# ---------- review.md ----------

@pytest.mark.parametrize(
    "title", ["acceptance criteria coverage", "gaps found", "test results", "pull request"]
)
def test_review_template_has_section(templates, title):
    assert title in headings(read(templates / "review.md"))


def test_review_template_maps_ac_to_code_and_test(templates):
    cov = section(read(templates / "review.md"), "acceptance criteria coverage").lower()
    for col in ("ac", "code", "test", "status"):
        assert col in cov


# ---------- CLAUDE.md templates ----------

def test_folder_claude_template_is_max_10_lines(templates):
    lines = read(templates / "folder-CLAUDE.md").strip().splitlines()
    assert 0 < len(lines) <= 10


def test_root_claude_template_points_at_spec_files(templates):
    text = read(templates / "root-CLAUDE.md")
    for ref in (".spec/spec.md", ".spec/plan.md", ".spec/state.json"):
        assert ref in text
    assert "red" in text.lower() and "green" in text.lower()


def test_root_claude_template_has_a_constraints_section(templates):
    # M9-AC3: the nearest thing SpecForge has to SPEC+'s shared constitution
    assert "## Constraints" in read(templates / "root-CLAUDE.md")


def test_skill_maps_spec_plus_to_specforge(skill_dir):
    # M9-AC4: a fixed table, not the model's own reading
    body = read(skill_dir / "SKILL.md")
    assert "## Importing SPEC+" in body
    section = body[body.index("## Importing SPEC+"):]
    for row in ("| S |", "| P table |", "| E1 |", "| E2 / E3 |", "| C table |", "| I |", "Constraints"):
        assert row in section, row
    assert "[Cn]" in section and "CLAUDE.md" in section
