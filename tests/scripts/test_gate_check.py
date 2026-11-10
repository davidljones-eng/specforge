import io
import json

import pytest

import gate_check
import state


# ---------- helpers ----------

def make_project(tmp_path, spec=False, plan=False):
    state.create(tmp_path)
    if spec:
        state.approve(tmp_path, "spec")
    if plan:
        state.approve(tmp_path, "plan")
    return tmp_path


def edit(proj, path, tool="Edit"):
    return {"tool_name": tool, "tool_input": {"file_path": str(path)}, "cwd": str(proj)}


def bash(proj, command):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(proj)}


def blocked(proj, event):
    return gate_check.evaluate(event, proj) is not None


SOURCE = ["src/app.py", "app/models/user.py", "lib/util.dart", "pkg/src/deep/x.py"]
TESTS = ["tests/test_x.py", "app/tests/test_y.py", "src/tests/test_z.py", "tests/unit/a/b.py"]
OTHER = ["README.md", "docs/guide.md", ".spec/spec.md", ".spec/plan.md", "CLAUDE.md", "pyproject.toml"]
DOC_IN_CODE_DIR = ["src/CLAUDE.md", "app/models/CLAUDE.md", "tests/CLAUDE.md"]


# ---------- no .spec: plugin is inactive ----------

@pytest.mark.parametrize("rel", SOURCE + TESTS + OTHER)
def test_project_without_spec_dir_is_never_blocked(tmp_path, rel):
    assert not blocked(tmp_path, edit(tmp_path, tmp_path / rel))


def test_bash_in_project_without_spec_dir_is_allowed(tmp_path):
    assert not blocked(tmp_path, bash(tmp_path, "echo x > .spec/state.json"))


# ---------- approval combinations ----------

@pytest.mark.parametrize("rel", SOURCE)
def test_source_blocked_until_both_approved(tmp_path, rel):
    proj = make_project(tmp_path)
    assert blocked(proj, edit(proj, proj / rel))
    state.approve(proj, "spec")
    assert blocked(proj, edit(proj, proj / rel))
    state.approve(proj, "plan")
    assert not blocked(proj, edit(proj, proj / rel))


@pytest.mark.parametrize("rel", TESTS)
def test_tests_open_once_spec_approved(tmp_path, rel):
    proj = make_project(tmp_path)
    assert blocked(proj, edit(proj, proj / rel))
    state.approve(proj, "spec")
    assert not blocked(proj, edit(proj, proj / rel))
    state.approve(proj, "plan")
    assert not blocked(proj, edit(proj, proj / rel))


@pytest.mark.parametrize("rel", DOC_IN_CODE_DIR)
def test_claude_md_in_code_dirs_needs_only_spec_approval(tmp_path, rel):
    proj = make_project(tmp_path)
    assert blocked(proj, edit(proj, proj / rel))
    state.approve(proj, "spec")
    assert not blocked(proj, edit(proj, proj / rel))


@pytest.mark.parametrize("rel", OTHER)
@pytest.mark.parametrize("spec,plan", [(False, False), (True, False), (True, True)])
def test_non_code_files_always_allowed(tmp_path, rel, spec, plan):
    proj = make_project(tmp_path, spec, plan)
    assert not blocked(proj, edit(proj, proj / rel))


def test_inconsistent_state_plan_without_spec_fails_closed(tmp_path):
    state.create(tmp_path)
    path = tmp_path / ".spec" / "state.json"
    data = json.loads(path.read_text())
    data["plan_approved"] = True
    path.write_text(json.dumps(data))
    reason = gate_check.evaluate(edit(tmp_path, tmp_path / "tests/t.py"), tmp_path)
    assert reason and "corrupt" in reason.lower()


# ---------- path handling ----------

def test_look_alike_dir_names_are_not_code_dirs(tmp_path):
    proj = make_project(tmp_path)
    for rel in ("srcs/x.py", "mysrc/x.py", "application/x.py", "library/x.py", "latest/x.py"):
        assert not blocked(proj, edit(proj, proj / rel)), rel


def test_a_file_named_like_a_code_dir_is_not_a_code_dir(tmp_path):
    proj = make_project(tmp_path)
    assert not blocked(proj, edit(proj, proj / "docs" / "src"))


def test_relative_path_resolved_against_cwd(tmp_path):
    proj = make_project(tmp_path)
    event = {"tool_name": "Write", "tool_input": {"file_path": "src/a.py"}, "cwd": str(proj)}
    assert blocked(proj, event)


def test_dotdot_cannot_dodge_the_gate(tmp_path):
    proj = make_project(tmp_path)
    assert blocked(proj, edit(proj, proj / "docs" / ".." / "src" / "a.py"))


def test_dotdot_cannot_dodge_state_protection(tmp_path):
    proj = make_project(tmp_path, True, True)
    assert blocked(proj, edit(proj, proj / "docs" / ".." / ".spec" / "state.json"))


def test_symlink_into_code_dir_is_resolved(tmp_path):
    proj = make_project(tmp_path)
    (proj / "src").mkdir()
    (proj / "alias").symlink_to(proj / "src")
    assert blocked(proj, edit(proj, proj / "alias" / "a.py"))


def test_path_outside_project_is_allowed(tmp_path):
    (tmp_path / "proj").mkdir()
    proj = make_project(tmp_path / "proj")
    other = tmp_path / "elsewhere" / "src" / "a.py"
    assert not blocked(proj, edit(proj, other))


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_all_edit_tools_are_gated(tmp_path, tool):
    proj = make_project(tmp_path)
    assert blocked(proj, edit(proj, proj / "src" / "a.py", tool=tool))


def test_non_edit_tools_are_ignored(tmp_path):
    proj = make_project(tmp_path)
    event = {"tool_name": "Read", "tool_input": {"file_path": str(proj / "src/a.py")}}
    assert not blocked(proj, event)


# ---------- state.json protection ----------

@pytest.mark.parametrize("spec,plan", [(False, False), (True, False), (True, True)])
def test_direct_edits_to_state_json_always_blocked(tmp_path, spec, plan):
    proj = make_project(tmp_path, spec, plan)
    reason = gate_check.evaluate(edit(proj, proj / ".spec" / "state.json"), proj)
    assert reason and "/specforge:approve" in reason


@pytest.mark.parametrize(
    "command",
    [
        "echo '{}' > .spec/state.json",
        "echo x >> .spec/state.json",
        "sed -i 's/false/true/' .spec/state.json",
        "tee .spec/state.json < other.json",
        "rm .spec/state.json",
        "mv /tmp/x .spec/state.json",
        "cp /tmp/x .spec/state.json",
        "python3 -c \"open('.spec/state.json','w').write('{}')\"",
        "truncate -s 0 .spec/state.json",
    ],
)
def test_bash_writes_to_state_json_blocked(tmp_path, command):
    proj = make_project(tmp_path)
    assert blocked(proj, bash(proj, command))


@pytest.mark.parametrize(
    "command",
    [
        "cat .spec/state.json",
        "jq . .spec/state.json",
        "cat .spec/state.json 2>&1",
        "grep phase .spec/state.json",
        "git status",
        "python3 scripts/state.py get",
        "python3 scripts/state.py set-phase build",
        "git commit -m 'feat: approve button'",
        "grep -rn approve src",
        "pytest -q",
    ],
)
def test_other_bash_commands_allowed(tmp_path, command):
    proj = make_project(tmp_path)
    assert not blocked(proj, bash(proj, command))


# ---------- only the user approves (M5-AC2) ----------

@pytest.mark.parametrize(
    "command",
    [
        "python3 scripts/state.py approve spec",
        'python3 "/home/u/.claude/plugins/specforge/scripts/state.py" approve spec',
        "python scripts/state.py  approve plan",
        "cd scripts && ./state.py approve spec",
        "python3 'scripts/state.py' 'approve' spec",
        'python3 scripts/st"ate".py approve spec',
        "python3 scripts/st\\ate.py approve spec",
        "python3 -c 'import state; state.approve(\".\", \"spec\")'",
        "python3 -c 'from state import approve; approve(\".\", \"plan\")'",
        "claude -p '/specforge:approve spec'",
        "echo /specforge:approve plan | claude -p",
    ],
)
def test_the_model_cannot_approve_through_bash(tmp_path, command):
    proj = make_project(tmp_path)
    reason = gate_check.evaluate(bash(proj, command), proj)
    assert reason is not None
    assert "only the user" in reason.lower() and "/specforge:approve" in reason


def skill(proj, name, args=None):
    tool_input = {"skill": name} if args is None else {"skill": name, "args": args}
    return {"tool_name": "Skill", "tool_input": tool_input, "cwd": str(proj)}


@pytest.mark.parametrize("name", ["specforge:approve", "/specforge:approve"])
def test_the_model_cannot_load_the_approve_command(tmp_path, name):
    # Seen in a real session: asked to "run /specforge:approve plan for me", the
    # model loaded it through the Skill tool despite disable-model-invocation.
    proj = make_project(tmp_path, spec=True)
    reason = gate_check.evaluate(skill(proj, name, "plan"), proj)
    assert reason is not None
    assert "only the user" in reason.lower() and "type" in reason.lower()


@pytest.mark.parametrize("name", ["specforge:status", "specforge:build", "approve", "other:approve"])
def test_other_skills_are_allowed(tmp_path, name):
    proj = make_project(tmp_path, spec=True)
    assert not blocked(proj, skill(proj, name))


def test_approving_through_bash_is_blocked_whatever_the_state(tmp_path):
    proj = make_project(tmp_path, spec=True, plan=True)
    assert blocked(proj, bash(proj, "python3 scripts/state.py approve plan"))


# ---------- fail safe ----------

def test_missing_state_with_spec_dir_blocks_code_with_helpful_reason(tmp_path):
    (tmp_path / ".spec").mkdir()
    reason = gate_check.evaluate(edit(tmp_path, tmp_path / "src/a.py"), tmp_path)
    assert reason and "/specforge:spec" in reason


def test_missing_state_with_spec_dir_still_allows_docs(tmp_path):
    (tmp_path / ".spec").mkdir()
    assert not blocked(tmp_path, edit(tmp_path, tmp_path / ".spec" / "spec.md"))


def test_corrupt_state_blocks_code(tmp_path):
    (tmp_path / ".spec").mkdir()
    (tmp_path / ".spec" / "state.json").write_text("{broken")
    reason = gate_check.evaluate(edit(tmp_path, tmp_path / "src/a.py"), tmp_path)
    assert reason and "corrupt" in reason.lower()


def test_edit_without_file_path_is_blocked(tmp_path):
    proj = make_project(tmp_path, True, True)
    assert blocked(proj, {"tool_name": "Edit", "tool_input": {}, "cwd": str(proj)})


def test_corrupt_config_blocks_code(tmp_path):
    proj = make_project(tmp_path, True, True)
    (proj / ".spec" / "config.json").write_text("{bad")
    reason = gate_check.evaluate(edit(proj, proj / "src/a.py"), proj)
    assert reason and "config" in reason.lower()


# ---------- reasons ----------

def test_reason_for_source_names_missing_spec_approval(tmp_path):
    proj = make_project(tmp_path)
    reason = gate_check.evaluate(edit(proj, proj / "src/a.py"), proj)
    assert "spec" in reason and "/specforge:approve spec" in reason and "src/a.py" in reason


def test_reason_for_source_names_missing_plan_approval(tmp_path):
    proj = make_project(tmp_path, spec=True)
    reason = gate_check.evaluate(edit(proj, proj / "src/a.py"), proj)
    assert "/specforge:approve plan" in reason and "/specforge:approve spec" not in reason


# ---------- config overrides ----------

def test_config_overrides_code_and_test_dirs(tmp_path):
    proj = make_project(tmp_path, spec=True)
    (proj / ".spec" / "config.json").write_text(
        json.dumps({"code_dirs": ["backend"], "test_dirs": ["checks"]})
    )
    assert blocked(proj, edit(proj, proj / "backend/a.py"))
    assert not blocked(proj, edit(proj, proj / "src/a.py"))
    assert not blocked(proj, edit(proj, proj / "checks/test_a.py"))
    assert not blocked(proj, edit(proj, proj / "tests/test_a.py"))


# ---------- main (hook entry point) ----------

def run_main(monkeypatch, proj, event, *argv):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    raw = event if isinstance(event, str) else json.dumps(event)
    return gate_check.main(list(argv), stdin=io.StringIO(raw))


def test_main_allows_with_exit_0_and_no_output(tmp_path, monkeypatch, capsys):
    proj = make_project(tmp_path, True, True)
    assert run_main(monkeypatch, proj, edit(proj, proj / "src/a.py")) == 0
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_main_blocks_with_exit_2_and_reason_on_stderr(tmp_path, monkeypatch, capsys):
    proj = make_project(tmp_path)
    assert run_main(monkeypatch, proj, edit(proj, proj / "src/a.py")) == 2
    assert "specforge" in capsys.readouterr().err


@pytest.mark.parametrize("raw", ["", "not json", "[]", "null", '"str"'])
def test_main_malformed_input_fails_closed(tmp_path, monkeypatch, capsys, raw):
    proj = make_project(tmp_path)
    assert run_main(monkeypatch, proj, raw) == 2
    assert capsys.readouterr().err


def test_main_internal_error_fails_closed(tmp_path, monkeypatch, capsys):
    proj = make_project(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(gate_check, "evaluate", boom)
    assert run_main(monkeypatch, proj, edit(proj, proj / "src/a.py")) == 2
    assert "kaboom" in capsys.readouterr().err


def test_main_falls_back_to_event_cwd_without_env(tmp_path, monkeypatch):
    proj = make_project(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    event = edit(proj, proj / "src/a.py")
    assert gate_check.main([], stdin=io.StringIO(json.dumps(event))) == 2


# ---------- post-edit hook: spec change bumps version ----------

def test_post_spec_edit_after_approval_bumps_and_resets(tmp_path, monkeypatch, capsys):
    proj = make_project(tmp_path, True, True)
    code = run_main(monkeypatch, proj, edit(proj, proj / ".spec" / "spec.md"), "--post")
    assert code == 0
    saved = state.read(proj)
    assert saved["spec_version"] == 2
    assert saved["spec_approved"] is False and saved["plan_approved"] is False
    out = json.loads(capsys.readouterr().out)
    assert "additionalContext" in out["hookSpecificOutput"]
    assert "approve" in out["hookSpecificOutput"]["additionalContext"]


def test_post_spec_edit_with_only_spec_approved_also_bumps(tmp_path, monkeypatch):
    proj = make_project(tmp_path, spec=True)
    run_main(monkeypatch, proj, edit(proj, proj / ".spec" / "spec.md", "Write"), "--post")
    assert state.read(proj)["spec_version"] == 2


def test_post_spec_edit_before_approval_does_not_bump(tmp_path, monkeypatch, capsys):
    proj = make_project(tmp_path)
    assert run_main(monkeypatch, proj, edit(proj, proj / ".spec" / "spec.md"), "--post") == 0
    assert state.read(proj)["spec_version"] == 1
    assert capsys.readouterr().out == ""


def test_repeated_spec_edits_bump_only_once_until_reapproved(tmp_path, monkeypatch):
    proj = make_project(tmp_path, True, True)
    for _ in range(3):
        run_main(monkeypatch, proj, edit(proj, proj / ".spec" / "spec.md"), "--post")
    assert state.read(proj)["spec_version"] == 2


@pytest.mark.parametrize("rel", ["src/a.py", ".spec/plan.md", ".spec/requirement.md", "spec.md", "docs/spec.md"])
def test_post_other_files_do_not_bump(tmp_path, monkeypatch, rel):
    proj = make_project(tmp_path, True, True)
    run_main(monkeypatch, proj, edit(proj, proj / rel), "--post")
    assert state.read(proj)["spec_version"] == 1


def test_post_ignores_non_edit_tools(tmp_path, monkeypatch):
    proj = make_project(tmp_path, True, True)
    event = {"tool_name": "Read", "tool_input": {"file_path": str(proj / ".spec/spec.md")}}
    run_main(monkeypatch, proj, event, "--post")
    assert state.read(proj)["spec_version"] == 1


def test_post_without_spec_dir_is_a_noop(tmp_path, monkeypatch):
    assert run_main(monkeypatch, tmp_path, edit(tmp_path, tmp_path / ".spec/spec.md"), "--post") == 0
    assert not (tmp_path / ".spec").exists()


@pytest.mark.parametrize("raw", ["", "garbage", "[]"])
def test_post_never_crashes_on_bad_input(tmp_path, monkeypatch, raw):
    proj = make_project(tmp_path, True, True)
    assert run_main(monkeypatch, proj, raw, "--post") == 0
    assert state.read(proj)["spec_version"] == 1


def test_post_with_corrupt_state_does_not_crash(tmp_path, monkeypatch):
    (tmp_path / ".spec").mkdir()
    (tmp_path / ".spec" / "state.json").write_text("{bad")
    assert run_main(monkeypatch, tmp_path, edit(tmp_path, tmp_path / ".spec/spec.md"), "--post") == 0
