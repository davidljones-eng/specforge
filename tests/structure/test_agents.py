import re

import pytest

from conftest import parse_frontmatter

AGENTS = ["spec-reviewer", "test-writer", "code-reviewer"]
REVIEWERS = ["spec-reviewer", "code-reviewer"]
READ_ONLY_TOOLS = {"Read", "Grep", "Glob"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"}
IGNORED_IN_PLUGINS = ("permissionMode", "hooks", "mcpServers", "initialPrompt")


def load(root, name):
    path = root / "agents" / f"{name}.md"
    assert path.is_file(), f"missing agents/{name}.md"
    meta, body = parse_frontmatter(path.read_text())
    return meta, body, body.lower()


def tools_of(meta):
    return {t.strip() for t in meta.get("tools", "").split(",") if t.strip()}


# ---------- structure ----------

def test_agents_live_at_plugin_root(root):
    assert not (root / ".claude-plugin" / "agents").exists()


def test_exactly_the_expected_agents_exist(root):
    assert sorted(p.stem for p in (root / "agents").glob("*.md")) == sorted(AGENTS)


@pytest.mark.parametrize("name", AGENTS)
def test_agent_frontmatter(root, name):
    meta, body, _ = load(root, name)
    assert meta.get("name") == name
    assert len(meta.get("description", "")) >= 30
    assert tools_of(meta), "agents must declare an explicit tools list"
    assert len(body.strip()) >= 300


@pytest.mark.parametrize("name", AGENTS)
def test_agent_does_not_use_fields_plugins_ignore(root, name):
    meta, _, _ = load(root, name)
    for field in IGNORED_IN_PLUGINS:
        assert field not in meta


def test_every_agent_a_command_launches_exists(root):
    referenced = set()
    for path in (root / "commands").glob("*.md"):
        referenced |= set(re.findall(r"(?<!/)specforge:([a-z][a-z-]*)", path.read_text()))
    assert referenced, "commands should launch agents"
    on_disk = {p.stem for p in (root / "agents").glob("*.md")}
    assert referenced <= on_disk, referenced - on_disk


# ---------- reviewers are read-only ----------

@pytest.mark.parametrize("name", REVIEWERS)
def test_reviewer_tools_are_read_only(root, name):
    meta, _, _ = load(root, name)
    tools = tools_of(meta)
    assert tools <= READ_ONLY_TOOLS, tools - READ_ONLY_TOOLS
    assert not tools & WRITE_TOOLS


@pytest.mark.parametrize("name", REVIEWERS)
def test_reviewer_prompt_says_it_must_not_modify_files(root, name):
    _, _, low = load(root, name)
    assert "read-only" in low
    assert "do not modify" in low or "never modify" in low


def test_spec_reviewer_checks_the_spec_quality_rules(root):
    _, body, low = load(root, "spec-reviewer")
    for needle in ("testable", "AC-", "out of scope", "edge cases", "ambiguous"):
        assert needle.lower() in low, needle
    assert "verdict" in low



def test_spec_reviewer_checks_fidelity_to_the_source(root):
    _, body, low = load(root, "spec-reviewer")
    assert ".spec/source.md" in body
    assert "fidelity" in low
    for needle in ("quoted", "number", "unchanged", "blocking"):
        assert needle in low, needle


def test_spec_reviewer_lists_source_fidelity_as_check_8(root):
    _, body, _ = load(root, "spec-reviewer")
    assert any(l.startswith("8. **Fidelity to the source.**") for l in body.splitlines())


def test_spec_reviewer_blocks_a_constraint_kept_only_in_the_requirement(root):
    # M7-AC4: in a re-check on Track C v2, only 1 run in 10 flagged that spec.md
    # had dropped "no runtime dependencies", which only requirement.md kept.
    _, body, _ = load(root, "spec-reviewer")
    check = next(l for l in body.splitlines() if "Fidelity to the source" in l).lower()
    assert "constraint" in check and "requirement.md" in check
    assert "scope" in check and "out of scope" in check
    assert "dropped" in check and "blocking" in check


def test_spec_reviewer_suggests_but_never_blocks_on_size(root):
    # M8-AC2
    _, body, _ = load(root, "spec-reviewer")
    check = next(l for l in body.splitlines() if l.startswith("9. "))
    low = check.lower()
    assert "spec_size.py" in check and "budget" in low
    assert "suggestion" in low and "never" in low and "blocking" in low
    assert "restate" in low


def test_spec_reviewer_skips_fidelity_when_it_is_turned_off(root):
    _, body, _ = load(root, "spec-reviewer")
    check = next(l for l in body.splitlines() if "Fidelity to the source" in l)
    assert '"source_fidelity": false' in check and "skip" in check.lower()

def test_code_reviewer_maps_every_ac_with_locations(root):
    _, body, low = load(root, "code-reviewer")
    assert "every ac" in low
    assert "file:line" in low
    assert "verdict" in low
    assert "independent" in low or "fresh" in low


# ---------- test-writer works from acceptance criteria only ----------

def test_test_writer_may_write_files_but_not_run_shell(root):
    meta, _, _ = load(root, "test-writer")
    tools = tools_of(meta)
    assert {"Read", "Write"} <= tools
    assert "Bash" not in tools


def test_test_writer_told_to_use_acceptance_criteria_only(root):
    _, _, low = load(root, "test-writer")
    assert "acceptance criteria only" in low
    assert "ac-" in low
    assert "failing" in low


def test_test_writer_must_not_peek_at_or_write_implementation(root):
    _, _, low = load(root, "test-writer")
    assert "do not read" in low and "implementation" in low
    assert "never write production code" in low or "do not write production code" in low


def test_test_writer_names_tests_after_ac_ids(root):
    _, _, low = load(root, "test-writer")
    assert "name" in low and "ac-id" in low


def test_test_writer_starts_each_name_with_its_own_ac_id(root):
    # the commit gate's test_filter selects tests by the AC-ID a name starts with
    _, _, low = load(root, "test-writer")
    assert "start each test's name with its own ac-id" in low
    assert "other acs" in low


def test_spec_reviewer_blocks_uncovered_source_checks(root):
    # M9-AC2
    _, body, _ = load(root, "spec-reviewer")
    check = next(l for l in body.splitlines() if l.startswith("10. "))
    assert "Uncovered checks" in check and "spec_size.py" in check
    assert "**blocking**" in check
