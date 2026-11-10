import io
import json

import pytest

import approve_hook
import state

SPEC = "# Spec\n\n## Acceptance criteria\n- AC-1: a\n- AC-2: b\n- AC-2 again, as in AC-1\n"
PLAN = "## Tasks\n- [ ] T1 (M1, AC-1): first\n- [ ] T2 (M1, AC-2): second\n"


@pytest.fixture
def proj(tmp_path):
    """A project whose spec has AC-IDs and whose plan exists, nothing approved."""
    state.create(tmp_path)
    (tmp_path / ".spec" / "spec.md").write_text(SPEC)
    (tmp_path / ".spec" / "plan.md").write_text(PLAN)
    return tmp_path


def approvals(proj):
    data = state.read(proj)
    return data["spec_approved"], data["plan_approved"]


def run(proj, prompt):
    return approve_hook.evaluate(prompt, proj)


# ---------- recording an approval (M5-AC1) ----------

def test_approve_spec_records_it(proj):
    decision, message = run(proj, "/specforge:approve spec")
    assert decision == "ok"
    assert approvals(proj) == (True, False)
    assert "spec" in message and "version 1" in message and "2 acceptance criteria" in message


def test_approve_plan_records_it(proj):
    state.approve(proj, "spec")
    decision, message = run(proj, "/specforge:approve plan")
    assert decision == "ok" and "plan" in message
    assert approvals(proj) == (True, True)


@pytest.mark.parametrize(
    "prompt", ["/specforge:approve  spec ", "/specforge:approve\tspec", "/specforge:approve spec\n"]
)
def test_spacing_claude_code_accepts_is_accepted(proj, prompt):
    # Claude Code runs the command for these too (M5 spike)
    assert run(proj, prompt)[0] == "ok"
    assert approvals(proj) == (True, False)


def test_a_spec_with_no_ac_ids_is_refused(proj):
    (proj / ".spec" / "spec.md").write_text("# Spec\n\nNo criteria yet.\n")
    decision, reason = run(proj, "/specforge:approve spec")
    assert decision == "block" and "AC-" in reason
    assert approvals(proj) == (False, False)


def test_a_missing_spec_is_refused(proj):
    (proj / ".spec" / "spec.md").unlink()
    decision, reason = run(proj, "/specforge:approve spec")
    assert decision == "block" and "spec.md" in reason and "/specforge:spec" in reason
    assert approvals(proj) == (False, False)


def test_the_plan_before_the_spec_is_refused(proj):
    decision, reason = run(proj, "/specforge:approve plan")
    assert decision == "block" and "/specforge:approve spec" in reason
    assert approvals(proj) == (False, False)


def test_a_missing_plan_is_refused(proj):
    state.approve(proj, "spec")
    (proj / ".spec" / "plan.md").unlink()
    decision, reason = run(proj, "/specforge:approve plan")
    assert decision == "block" and "plan.md" in reason and "/specforge:plan" in reason
    assert approvals(proj) == (True, False)


def test_no_spec_folder_is_refused(tmp_path):
    decision, reason = run(tmp_path, "/specforge:approve spec")
    assert decision == "block" and "/specforge:spec" in reason
    assert not (tmp_path / ".spec").exists()


def test_missing_state_is_refused(proj):
    (proj / ".spec" / "state.json").unlink()
    decision, reason = run(proj, "/specforge:approve spec")
    assert decision == "block" and "/specforge:spec" in reason
    assert not (proj / ".spec" / "state.json").exists()


def test_corrupt_state_is_refused_and_left_alone(proj):
    (proj / ".spec" / "state.json").write_text("{broken")
    decision, reason = run(proj, "/specforge:approve spec")
    assert decision == "block" and "corrupt" in reason.lower()
    assert (proj / ".spec" / "state.json").read_text() == "{broken"


# ---------- prompts that are not the command, or misuse it (M5-AC3) ----------

@pytest.mark.parametrize(
    "prompt",
    [
        "looks good, approve it",
        "approve spec",
        " /specforge:approve spec",  # leading space: Claude Code does not run the command
        "please run /specforge:approve spec",
        "/approve spec",
        "/specforge:approver spec",
        "/specforge:status",
        "",
    ],
)
def test_a_prompt_that_does_not_run_the_command_changes_nothing(proj, prompt):
    assert run(proj, prompt) is None
    assert approvals(proj) == (False, False)


@pytest.mark.parametrize(
    "prompt",
    [
        "/specforge:approve",
        "/specforge:approve spec please",
        "/specforge:approve spec plan",
        "/specforge:approve build",
        "/specforge:approve SPEC",
        "/specforge:approve spec\nand also start planning",
    ],
)
def test_a_bad_target_is_refused_with_the_usage_line(proj, prompt):
    decision, reason = run(proj, prompt)
    assert decision == "block" and "/specforge:approve spec|plan" in reason
    assert approvals(proj) == (False, False)


# ---------- the hook process ----------

def main(proj, monkeypatch, event, env=True):
    if env:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    else:
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    text = event if isinstance(event, str) else json.dumps(event)
    return approve_hook.main(io.StringIO(text))


def prompt_event(proj, prompt):
    return {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "cwd": str(proj)}


def test_main_records_and_tells_the_model(proj, monkeypatch, capsys):
    assert main(proj, monkeypatch, prompt_event(proj, "/specforge:approve spec")) == 0
    out = json.loads(capsys.readouterr().out)
    context = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "recorded" in context
    assert approvals(proj) == (True, False)


def test_main_blocks_the_prompt_with_the_reason(proj, monkeypatch, capsys):
    assert main(proj, monkeypatch, prompt_event(proj, "/specforge:approve plan")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "/specforge:approve spec" in out["reason"]
    assert approvals(proj) == (False, False)


def test_main_says_nothing_about_other_prompts(proj, monkeypatch, capsys):
    assert main(proj, monkeypatch, prompt_event(proj, "looks good, approve it")) == 0
    assert capsys.readouterr().out == ""
    assert approvals(proj) == (False, False)


@pytest.mark.parametrize("event", ["{broken", "[]", {"hook_event_name": "UserPromptSubmit"}])
def test_main_with_unreadable_input_records_nothing_and_blocks_nothing(
    proj, monkeypatch, capsys, event
):
    assert main(proj, monkeypatch, event) == 0
    assert capsys.readouterr().out == ""
    assert approvals(proj) == (False, False)


def test_main_uses_the_event_cwd_without_claude_project_dir(proj, monkeypatch, capsys):
    assert main(proj, monkeypatch, prompt_event(proj, "/specforge:approve spec"), env=False) == 0
    assert approvals(proj) == (True, False)


def test_an_unexpected_error_blocks_the_approval(proj, monkeypatch, capsys):
    def boom(*_):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(state, "approve", boom)
    assert main(proj, monkeypatch, prompt_event(proj, "/specforge:approve spec")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "disk on fire" in out["reason"]
    assert approvals(proj) == (False, False)


# ---------- withdrawing an approval (M6) ----------

@pytest.fixture
def built(proj):
    """Both approved, mid-build, with a RED record (M4)."""
    state.approve(proj, "spec")
    state.approve(proj, "plan")
    state.update(proj, phase="build")
    state.record_red(proj, "T1", "abc1234")
    return proj


def test_unapprove_plan_withdraws_only_the_plan(built):
    decision, message = run(built, "/specforge:unapprove plan")
    assert decision == "ok" and "withdrew" in message and "plan" in message
    data = state.read(built)
    assert (data["spec_approved"], data["plan_approved"]) == (True, False)
    assert data["phase"] == "plan" and data["spec_version"] == 1
    assert state.red_tasks(data) == {"T1": "abc1234"}  # kept; re-approving clears them


def test_unapprove_spec_withdraws_both(built):
    decision, message = run(built, "/specforge:unapprove spec")
    assert decision == "ok" and "withdrew" in message and "spec" in message
    data = state.read(built)
    assert (data["spec_approved"], data["plan_approved"]) == (False, False)
    assert data["phase"] == "spec" and data["spec_version"] == 1


def test_unapprove_spec_with_only_the_spec_approved(proj):
    state.approve(proj, "spec")
    assert run(proj, "/specforge:unapprove spec")[0] == "ok"
    assert approvals(proj) == (False, False)


@pytest.mark.parametrize("target", ["spec", "plan"])
def test_nothing_to_withdraw_is_refused(proj, target):
    before = (proj / ".spec" / "state.json").read_text()
    decision, reason = run(proj, f"/specforge:unapprove {target}")
    assert decision == "block" and "nothing to withdraw" in reason.lower()
    assert (proj / ".spec" / "state.json").read_text() == before


def test_unapprove_plan_when_only_the_spec_is_approved_is_refused(proj):
    state.approve(proj, "spec")
    decision, reason = run(proj, "/specforge:unapprove plan")
    assert decision == "block" and "nothing to withdraw" in reason.lower()
    assert approvals(proj) == (True, False)


@pytest.mark.parametrize(
    "prompt", ["/specforge:unapprove", "/specforge:unapprove plan now", "/specforge:unapprove build"]
)
def test_a_bad_unapprove_target_is_refused_with_its_usage_line(built, prompt):
    decision, reason = run(built, prompt)
    assert decision == "block" and "/specforge:unapprove spec|plan" in reason
    assert approvals(built) == (True, True)


@pytest.mark.parametrize(
    "prompt",
    [" /specforge:unapprove plan", "please /specforge:unapprove plan", "/specforge:unapproved plan",
     "withdraw the plan approval"],
)
def test_a_prompt_that_does_not_run_unapprove_changes_nothing(built, prompt):
    assert run(built, prompt) is None
    assert approvals(built) == (True, True)


def test_unapprove_with_no_state_is_refused(tmp_path):
    (tmp_path / ".spec").mkdir()
    decision, reason = run(tmp_path, "/specforge:unapprove plan")
    assert decision == "block" and "/specforge:spec" in reason


def test_main_tells_the_model_what_was_withdrawn(built, monkeypatch, capsys):
    assert main(built, monkeypatch, prompt_event(built, "/specforge:unapprove plan")) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "withdrew" in context and "plan" in context
    assert approvals(built) == (True, False)
