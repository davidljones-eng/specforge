import re

import pytest

from conftest import parse_frontmatter

COMMANDS = ["spec", "plan", "build", "review", "status", "approve"]
PLUGIN_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)")


def load(root, name):
    path = root / "commands" / f"{name}.md"
    assert path.is_file(), f"missing commands/{name}.md"
    meta, body = parse_frontmatter(path.read_text())
    return meta, body, body.lower()


# ---------- structure ----------

def test_commands_live_at_plugin_root(root):
    assert not (root / ".claude-plugin" / "commands").exists()


def test_exactly_the_expected_commands_exist(root):
    found = sorted(p.stem for p in (root / "commands").glob("*.md"))
    assert found == sorted(COMMANDS)


@pytest.mark.parametrize("name", COMMANDS)
def test_command_has_frontmatter_and_description(root, name):
    meta, body, _ = load(root, name)
    assert len(meta.get("description", "")) >= 20, "description required"
    assert len(body.strip()) >= 300, "command body looks empty"


@pytest.mark.parametrize("name", COMMANDS)
def test_command_scripts_and_templates_referenced_exist(root, name):
    _, body, _ = load(root, name)
    for ref in PLUGIN_ROOT_REF.findall(body):
        assert (root / ref.rstrip(".,;:)")).exists(), f"{name}.md references missing {ref}"


@pytest.mark.parametrize("name", ["plan", "build", "review", "status", "approve"])
def test_state_driven_commands_use_state_script(root, name):
    _, body, _ = load(root, name)
    assert "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" in body


@pytest.mark.parametrize("name", ["plan", "build", "review"])
def test_phase_commands_record_their_phase(root, name):
    _, body, _ = load(root, name)
    assert f"set-phase {name}" in body


# ---------- /specforge:spec ----------

def test_spec_takes_a_description(root):
    meta, body, _ = load(root, "spec")
    assert "description" in meta["argument-hint"].lower()
    assert "$ARGUMENTS" in body


def test_spec_workflow_wording(root):
    _, body, low = load(root, "spec")
    assert "5 clarifying questions" in low or "five clarifying questions" in low
    for needle in ("requirement.md", "spec.md", "CLAUDE.md", "AC-1", "spec-reviewer"):
        assert needle in body, needle
    assert "state.py" in body and "init" in body
    assert "stop" in low and "/specforge:approve spec" in body


def test_spec_uses_the_templates(root):
    _, body, _ = load(root, "spec")
    for tpl in ("requirement.md", "spec.md", "root-CLAUDE.md"):
        assert f"skills/spec-writing/templates/{tpl}" in body



def test_spec_saves_the_description_verbatim_as_the_source(root):
    _, body, low = load(root, "spec")
    assert ".spec/source.md" in body
    assert "verbatim" in low
    # it must be saved before anything is derived from it
    assert body.index(".spec/source.md") < body.index("skills/spec-writing/templates/requirement.md")


def test_spec_reviewer_is_given_the_source(root):
    _, body, _ = load(root, "spec")
    review_step = next(l for l in body.splitlines() if "spec-reviewer" in l and "subagent" in l)
    assert ".spec/source.md" in review_step


def test_spec_can_turn_source_fidelity_off(root):
    # "source_fidelity": false gives the original spec phase: no source.md,
    # no copy-exactly rule, and the reviewer is not given a source
    _, body, _ = load(root, "spec")
    assert '"source_fidelity": false' in body
    assert body.index(".spec/config.json") < body.index("verbatim")
    save_step = next(l for l in body.splitlines() if "verbatim" in l and ".spec/source.md" in l)
    assert "source_fidelity" in save_step and "skip" in save_step.lower()
    review_step = next(l for l in body.splitlines() if "spec-reviewer" in l and "subagent" in l)
    assert "source_fidelity" in review_step

# ---------- /specforge:plan ----------

def test_plan_refuses_when_spec_not_approved(root):
    _, body, low = load(root, "plan")
    assert "refuse" in low
    assert "spec_approved" in body
    assert "/specforge:approve spec" in body


def test_plan_workflow_wording(root):
    _, body, low = load(root, "plan")
    for needle in ("plan.md", "AC-", "test-writer", "CLAUDE.md"):
        assert needle in body, needle
    assert "10 lines" in low
    assert "failing" in low
    assert "skills/spec-writing/templates/plan.md" in body
    assert "skills/spec-writing/templates/folder-CLAUDE.md" in body
    assert "stop" in low and "/specforge:approve plan" in body


# ---------- /specforge:build ----------

def test_build_accepts_mode_flag(root):
    meta, body, _ = load(root, "build")
    hint = meta["argument-hint"]
    assert "--mode" in hint
    for mode in ("strict", "checkpoint", "auto"):
        assert mode in hint
    assert "$ARGUMENTS" in body


def test_build_refuses_unless_both_approved(root):
    _, body, low = load(root, "build")
    assert "refuse" in low
    assert "spec_approved" in body and "plan_approved" in body
    assert "/specforge:approve spec" in body and "/specforge:approve plan" in body


def test_build_workflow_wording(root):
    _, body, low = load(root, "build")
    assert "branch" in low
    for step in ("red", "green", "refactor"):
        assert step in low
    for prefix in ("test:", "feat:", "refactor:"):
        assert prefix in body
    assert "set-mode" in body
    assert "3 times" in low or "3 failures" in low or "three" in low
    assert "never edit .spec/state.json" in low


def test_build_describes_all_three_modes(root):
    _, _, low = load(root, "build")
    assert "strict" in low and "every task" in low
    assert "checkpoint" in low and "milestone" in low
    assert "auto" in low and "risky" in low


# ---------- /specforge:review ----------

def test_review_wording(root):
    _, body, low = load(root, "review")
    assert "code-reviewer" in body
    assert "review.md" in body and "skills/spec-writing/templates/review.md" in body
    assert "gh pr create" in body
    assert "never merge" in low
    assert "fresh" in low


def test_review_maps_every_ac_and_reports_test_results(root):
    _, _, low = load(root, "review")
    assert "every ac" in low
    assert "full test" in low


def test_review_checks_red_before_green_in_git_history(root):
    _, body, low = load(root, "review")
    assert "git log" in body
    assert "test:" in body and "feat:" in body
    assert "before" in low


# ---------- /specforge:status ----------

def test_status_reports_everything(root):
    _, body, low = load(root, "status")
    for needle in ("phase", "approv", "spec version", "pending"):
        assert needle in low, needle
    assert "state.py" in body and "get" in body


def test_status_is_read_only(root):
    _, body, low = load(root, "status")
    assert "read-only" in low or "do not modify" in low


# ---------- /specforge:approve ----------

def test_approve_cannot_be_invoked_by_the_model(root):
    meta, _, _ = load(root, "approve")
    assert meta.get("disable-model-invocation") == "true"


def test_approve_takes_spec_or_plan(root):
    meta, body, _ = load(root, "approve")
    assert "spec|plan" in meta["argument-hint"]
    assert "$ARGUMENTS" in body
    assert "state.py" in body and "approve" in body


def test_approve_validates_target_and_prerequisites(root):
    _, _, low = load(root, "approve")
    assert "spec.md" in low and "plan.md" in low
    assert "only" in low and "spec" in low and "plan" in low
    assert "refuse" in low
    assert "next" in low  # tells the user what to run next


def test_spec_writer_carries_source_constraints_into_the_spec(root):
    # M7-AC4: the constraint goes in spec.md, not only in requirement.md
    _, body, _ = load(root, "spec")
    step = next(l for l in body.splitlines() if "templates/spec.md" in l)
    low = step.lower()
    assert "constraint" in low and "requirement.md" in low
    assert "scope" in low and "out of scope" in low


# ---------- AC budget (M8) ----------

def test_spec_measures_the_spec_before_the_review(root):
    _, body, _ = load(root, "spec")
    script = "${CLAUDE_PLUGIN_ROOT}/scripts/spec_size.py"
    assert script in body
    review = body.index("specforge:spec-reviewer")
    assert body.index("templates/spec.md") < body.index(script) < review
    step = next(l for l in body.splitlines() if "specforge:spec-reviewer" in l)
    assert "spec_size.py" in step  # the reviewer gets the line


def test_spec_shows_the_size_line_in_its_summary(root):
    _, body, _ = load(root, "spec")
    stop = next(l for l in body.splitlines() if l.lstrip().startswith("7. **Stop.**"))
    assert "spec_size.py" in stop and "suggestion" in stop.lower()


def test_spec_writer_does_not_cut_acs_to_meet_the_budget(root):
    # M8-AC3: merging ACs or splitting the feature is the user's call
    _, body, low = load(root, "spec")
    step = next(l for l in body.splitlines() if "specforge:spec-reviewer" in l).lower()
    assert "budget" in step and "merge" in step and "user" in step


# ---------- importing SPEC+ (M9) ----------

def test_spec_takes_from_files(root):
    meta, body, _ = load(root, "spec")
    assert "--from <file>" in meta["argument-hint"]
    assert "--from" in body


def test_spec_copies_from_files_with_the_import_script(root):
    # M9-AC1: a script copies them, not the model
    _, body, _ = load(root, "spec")
    step = next(l for l in body.splitlines() if "verbatim" in l and ".spec/source.md" in l)
    assert "${CLAUDE_PLUGIN_ROOT}/scripts/import_source.py" in step
    assert "source_fidelity" in step


def test_spec_points_to_the_spec_plus_mapping(root):
    # M9-AC4
    _, body, _ = load(root, "spec")
    assert "Importing SPEC+" in body


def test_spec_fills_the_root_claude_md_constraints(root):
    # M9-AC3
    _, body, _ = load(root, "spec")
    step = next(l for l in body.splitlines() if "templates/root-CLAUDE.md" in l)
    assert "Constraints" in step and "E4" in step


# ---------- source IDs (M10) ----------

def test_plan_checks_the_traceability_table_before_stopping(root):
    _, body, _ = load(root, "plan")
    script = "${CLAUDE_PLUGIN_ROOT}/scripts/trace_check.py\" --plan"
    assert script in body
    stop = body.index("**Stop.**")
    assert body.index(script) < stop
    step = next(l for l in body.splitlines() if "traceability table" in l)
    assert "Source" in step


def test_review_puts_the_rollup_in_review_md(root):
    _, body, _ = load(root, "review")
    script = "${CLAUDE_PLUGIN_ROOT}/scripts/trace_check.py\" --rollup"
    assert script in body
    step = next(l for l in body.splitlines() if "--rollup" in l)
    assert "Source Checks" in step
