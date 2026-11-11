import json
import subprocess
import sys

import pytest

import gate_check
import state
import test_gate

PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"
KNOWN_EVENTS = {"PreToolUse", "PostToolUse", "UserPromptSubmit"}


@pytest.fixture(scope="module")
def hooks_file(root):
    path = root / "hooks" / "hooks.json"
    assert path.is_file(), "missing hooks/hooks.json"
    return json.loads(path.read_text())


def all_hooks(hooks_file):
    """Yield (event, matcher, hook) for every hook handler."""
    for event, groups in hooks_file["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                yield event, group.get("matcher", ""), hook


def find(hooks_file, event, script, matcher=None):
    found = [
        (m, h) for e, m, h in all_hooks(hooks_file)
        if e == event and any(script in a for a in h.get("args", []))
        and (matcher is None or m == matcher)
    ]
    assert found, f"no {event} hook running {script} (matcher={matcher})"
    return found


# ---------- format ----------

def test_hooks_json_has_hooks_wrapper(hooks_file):
    assert isinstance(hooks_file["hooks"], dict) and hooks_file["hooks"]


def test_hooks_json_not_in_claude_plugin_dir(root):
    assert not (root / ".claude-plugin" / "hooks.json").exists()


def test_plugin_json_does_not_redeclare_default_hooks_file(plugin_json):
    assert "hooks" not in plugin_json


def test_only_known_events_are_used(hooks_file):
    assert set(hooks_file["hooks"]) <= KNOWN_EVENTS


def test_every_hook_is_a_well_formed_command(hooks_file):
    for _, _, hook in all_hooks(hooks_file):
        assert hook["type"] == "command"
        assert hook["command"] == "python3"
        assert isinstance(hook["args"], list) and all(isinstance(a, str) for a in hook["args"])
        assert isinstance(hook["timeout"], int) and hook["timeout"] > 0


def test_every_hook_script_exists(hooks_file, root):
    for _, _, hook in all_hooks(hooks_file):
        script = hook["args"][0]
        assert script.startswith(f"{PLUGIN_ROOT_VAR}/scripts/"), script
        assert (root / script.replace(PLUGIN_ROOT_VAR + "/", "")).is_file(), script


# ---------- coverage ----------

def test_edit_gate_covers_every_edit_tool(hooks_file):
    (matcher, _), = find(hooks_file, "PreToolUse", "gate_check.py", matcher="Edit|Write|MultiEdit")
    assert set(matcher.split("|")) == set(gate_check.EDIT_TOOLS)


def test_bash_is_guarded_by_gate_check_and_test_gate(hooks_file):
    find(hooks_file, "PreToolUse", "gate_check.py", matcher="Bash")
    find(hooks_file, "PreToolUse", "test_gate.py", matcher="Bash")


def test_post_edit_hook_runs_gate_check_in_post_mode(hooks_file):
    (matcher, hook), = find(hooks_file, "PostToolUse", "gate_check.py")
    assert set(matcher.split("|")) == set(gate_check.EDIT_TOOLS)
    assert "--post" in hook["args"]


def test_approvals_are_recorded_by_a_user_prompt_hook(hooks_file):
    # M5: fires only on prompts a person submits
    ((matcher, hook),) = find(hooks_file, "UserPromptSubmit", "approve_hook.py")
    assert matcher == "" and "if" not in hook


def test_the_skill_tool_is_guarded_by_gate_check(hooks_file):
    # the model can load /specforge:approve through the Skill tool (M5-AC3)
    find(hooks_file, "PreToolUse", "gate_check.py", matcher="Skill")


def test_the_prompt_hook_does_not_block_every_prompt_when_it_fails(hooks_file):
    # A crash must not lock the user out of the session; the script blocks
    # failed approvals itself, and a failure records nothing.
    ((_, hook),) = find(hooks_file, "UserPromptSubmit", "approve_hook.py")
    assert hook.get("onFailure") != "block"


def test_pre_hooks_do_not_use_if_filters_that_could_miss_commits(hooks_file):
    for event, _, hook in all_hooks(hooks_file):
        if event == "PreToolUse":
            assert "if" not in hook


# ---------- fail safe ----------

def test_pre_hooks_block_on_failure(hooks_file):
    for event, _, hook in all_hooks(hooks_file):
        if event == "PreToolUse":
            assert hook.get("onFailure") == "block"


def test_test_gate_timeout_outlasts_the_test_run(hooks_file):
    ((_, hook),) = find(hooks_file, "PreToolUse", "test_gate.py")
    assert hook["timeout"] > test_gate.TEST_TIMEOUT


# ---------- wiring: run the real commands from hooks.json ----------

def run_hook(hook, root, project, event):
    args = [a.replace(PLUGIN_ROOT_VAR, str(root)) for a in hook["args"]]
    return subprocess.run(
        [sys.executable, *args],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(project), "PATH": "/usr/bin:/bin"},
        timeout=30,
    )


def test_edit_hook_blocks_then_allows_when_run_as_a_real_process(hooks_file, root, tmp_path):
    state.create(tmp_path)
    ((_, hook),) = find(hooks_file, "PreToolUse", "gate_check.py", matcher="Edit|Write|MultiEdit")
    event = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "src/a.py")},
             "cwd": str(tmp_path)}
    blocked = run_hook(hook, root, tmp_path, event)
    assert blocked.returncode == 2 and "/specforge:approve spec" in blocked.stderr
    state.approve(tmp_path, "spec")
    state.approve(tmp_path, "plan")
    assert run_hook(hook, root, tmp_path, event).returncode == 0


def test_commit_hook_blocks_failing_tests_when_run_as_a_real_process(hooks_file, root, tmp_path):
    state.create(tmp_path)
    state.approve(tmp_path, "spec")
    state.approve(tmp_path, "plan")
    (tmp_path / ".spec" / "config.json").write_text(json.dumps({"test_command": "exit 1"}))
    ((_, hook),) = find(hooks_file, "PreToolUse", "test_gate.py")
    event = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'feat: x'"},
             "cwd": str(tmp_path)}
    assert run_hook(hook, root, tmp_path, event).returncode == 2
    event["tool_input"]["command"] = "git commit -m 'test: x'"
    assert run_hook(hook, root, tmp_path, event).returncode == 0


def test_prompt_hook_records_approval_when_run_as_a_real_process(hooks_file, root, tmp_path):
    state.create(tmp_path)
    (tmp_path / ".spec" / "spec.md").write_text("- AC-1: x\n")
    ((_, hook),) = find(hooks_file, "UserPromptSubmit", "approve_hook.py")
    event = {"hook_event_name": "UserPromptSubmit", "prompt": "/specforge:approve plan",
             "cwd": str(tmp_path)}
    refused = run_hook(hook, root, tmp_path, event)
    assert refused.returncode == 0 and json.loads(refused.stdout)["decision"] == "block"
    event["prompt"] = "/specforge:approve spec"
    assert run_hook(hook, root, tmp_path, event).returncode == 0
    assert state.read(tmp_path)["spec_approved"] is True


def test_post_hook_bumps_version_when_run_as_a_real_process(hooks_file, root, tmp_path):
    state.create(tmp_path)
    state.approve(tmp_path, "spec")
    ((_, hook),) = find(hooks_file, "PostToolUse", "gate_check.py")
    event = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / ".spec/spec.md")},
             "cwd": str(tmp_path)}
    result = run_hook(hook, root, tmp_path, event)
    assert result.returncode == 0
    assert state.read(tmp_path)["spec_version"] == 2


# ---------- holdout reads (M11) ----------

def test_reads_and_searches_are_guarded_by_gate_check(hooks_file):
    (matcher, hook), = find(hooks_file, "PreToolUse", "gate_check.py", matcher="Read|Grep|Glob")
    assert hook.get("onFailure") == "block"
