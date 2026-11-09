import io
import re
from pathlib import Path
import json

import pytest

import state
import test_gate


# ---------- commit detection ----------

@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'x'",
        "git add -A && git commit -m x",
        "git -C /some/path commit -m x",
        "git commit --amend --no-edit",
        "cd app && git commit -am 'x'",
    ],
)
def test_is_commit_true(command):
    assert test_gate.is_commit(command)


@pytest.mark.parametrize(
    "command",
    ["git status", "git log --grep commit", "git commit-tree abc", "pytest -q", "echo hi", ""],
)
def test_is_commit_false(command):
    assert not test_gate.is_commit(command)


# ---------- test: prefix ----------

HEREDOC = """git commit -m "$(cat <<'EOF'
test: add state tests

Co-Authored-By: someone
EOF
)\""""


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'test: add x'",
        'git commit -m "test: add x"',
        "git commit -am 'test: add x'",
        "git commit --message='test: add x'",
        'git commit --message "test: add x"',
        "git commit -m 'test(api): add x'",
        "git add -A && git commit -m 'test: add x'",
        HEREDOC,
    ],
)
def test_only_test_commits_true(command):
    assert test_gate.only_test_commits(command)


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'feat: add x'",
        "git commit -m 'add test: x'",
        "git commit -m 'tests: x'",
        "git commit -m 'testing: x'",
        "git commit --amend --no-edit",
        "git commit -F msg.txt",
        "git commit",
        "git commit -m 'test: a' && git commit -m 'feat: b'",
        "git commit -m 'feat: b' && git commit -m 'test: a'",
    ],
)
def test_only_test_commits_false(command):
    assert not test_gate.only_test_commits(command)


# ---------- command detection and resolution ----------

def no_which(_):
    return None


def all_which(name):
    return f"/usr/bin/{name}"


def test_detect_pytest_by_ini(tmp_path):
    (tmp_path / "pytest.ini").write_text("")
    assert test_gate.detect_test_command(tmp_path) == "pytest -q"


def test_detect_pytest_by_pyproject_section(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    assert test_gate.detect_test_command(tmp_path) == "pytest -q"


def test_pyproject_without_pytest_section_is_not_enough(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert test_gate.detect_test_command(tmp_path) is None


def test_detect_pytest_by_conftest_or_tests_dir(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    assert test_gate.detect_test_command(tmp_path) == "pytest -q"


def test_detect_npm(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    assert test_gate.detect_test_command(tmp_path) == "npm test"


def test_package_json_without_test_script_ignored(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "x"}}))
    assert test_gate.detect_test_command(tmp_path) is None


def test_broken_package_json_ignored(tmp_path):
    (tmp_path / "package.json").write_text("{bad")
    assert test_gate.detect_test_command(tmp_path) is None


def test_detect_flutter(tmp_path):
    (tmp_path / "pubspec.yaml").write_text("name: x\n")
    assert test_gate.detect_test_command(tmp_path) == "flutter test"


def test_detect_priority_pytest_then_npm_then_flutter(tmp_path):
    (tmp_path / "pubspec.yaml").write_text("name: x\n")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "x"}}))
    assert test_gate.detect_test_command(tmp_path) == "npm test"
    (tmp_path / "pytest.ini").write_text("")
    assert test_gate.detect_test_command(tmp_path) == "pytest -q"


def test_detect_nothing(tmp_path):
    assert test_gate.detect_test_command(tmp_path) is None


def test_resolve_prefers_configured_command(tmp_path):
    (tmp_path / "pytest.ini").write_text("")
    cmd, err = test_gate.resolve_command(tmp_path, {"test_command": "make test"}, no_which)
    assert (cmd, err) == ("make test", None)


def test_resolve_detected_command_with_runner_present(tmp_path):
    (tmp_path / "pytest.ini").write_text("")
    assert test_gate.resolve_command(tmp_path, {"test_command": None}, all_which) == ("pytest -q", None)


def test_resolve_detected_command_with_runner_missing(tmp_path):
    (tmp_path / "pytest.ini").write_text("")
    cmd, err = test_gate.resolve_command(tmp_path, {"test_command": None}, no_which)
    assert cmd is None and "pytest" in err and "not found" in err


def test_resolve_nothing_detected_points_at_config(tmp_path):
    cmd, err = test_gate.resolve_command(tmp_path, {"test_command": None}, all_which)
    assert cmd is None and "test_command" in err and ".spec/config.json" in err


# ---------- hook entry point ----------

@pytest.fixture
def proj(tmp_path):
    state.create(tmp_path)
    state.approve(tmp_path, "spec")
    state.approve(tmp_path, "plan")
    return tmp_path


def set_test_command(proj, command):
    (proj / ".spec" / "config.json").write_text(json.dumps({"test_command": command}))


def commit_event(proj, message="feat: add thing"):
    return {
        "tool_name": "Bash",
        "tool_input": {"command": f"git commit -m '{message}'"},
        "cwd": str(proj),
    }


def run_gate(monkeypatch, proj, event):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    raw = event if isinstance(event, str) else json.dumps(event)
    return test_gate.main([], stdin=io.StringIO(raw))


MARKER = "touch ran.marker"


def test_passing_tests_allow_commit(proj, monkeypatch, capsys):
    set_test_command(proj, f"{MARKER} && exit 0")
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 0
    assert (proj / "ran.marker").exists()
    assert capsys.readouterr().err == ""


def test_failing_tests_block_commit_and_show_output(proj, monkeypatch, capsys):
    set_test_command(proj, "echo SOME-FAILURE-OUTPUT; exit 1")
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    err = capsys.readouterr().err
    assert "tests failed" in err.lower()
    assert "SOME-FAILURE-OUTPUT" in err
    assert "test:" in err  # tells the user how to commit failing tests


def test_stderr_of_test_command_is_included(proj, monkeypatch, capsys):
    set_test_command(proj, "echo ERR-TEXT >&2; exit 1")
    run_gate(monkeypatch, proj, commit_event(proj))
    assert "ERR-TEXT" in capsys.readouterr().err


def test_long_output_is_truncated_to_the_tail(proj, monkeypatch, capsys):
    set_test_command(proj, "seq 1 5000; exit 1")
    run_gate(monkeypatch, proj, commit_event(proj))
    err = capsys.readouterr().err
    assert "5000" in err and "\n2500\n" not in err
    assert len(err) < 10000


def test_test_prefixed_commit_skips_tests(proj, monkeypatch):
    set_test_command(proj, f"{MARKER} && exit 1")
    assert run_gate(monkeypatch, proj, commit_event(proj, "test: add failing test")) == 0
    assert not (proj / "ran.marker").exists()


def test_chained_test_and_feat_commits_still_run_tests(proj, monkeypatch):
    set_test_command(proj, f"{MARKER} && exit 1")
    event = commit_event(proj)
    event["tool_input"]["command"] = "git commit -m 'test: a' && git commit -m 'feat: b'"
    assert run_gate(monkeypatch, proj, event) == 2
    assert (proj / "ran.marker").exists()


def test_non_commit_bash_is_ignored(proj, monkeypatch):
    set_test_command(proj, f"{MARKER} && exit 1")
    event = {"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(proj)}
    assert run_gate(monkeypatch, proj, event) == 0
    assert not (proj / "ran.marker").exists()


def test_non_bash_tool_is_ignored(proj, monkeypatch):
    set_test_command(proj, "exit 1")
    event = {"tool_name": "Edit", "tool_input": {"file_path": "x"}, "cwd": str(proj)}
    assert run_gate(monkeypatch, proj, event) == 0


def test_project_without_spec_dir_is_ignored(tmp_path, monkeypatch):
    assert run_gate(monkeypatch, tmp_path, commit_event(tmp_path)) == 0


@pytest.mark.parametrize("approve_spec,approve_plan", [(False, False), (True, False)])
def test_gate_is_inactive_before_both_approvals(tmp_path, monkeypatch, approve_spec, approve_plan):
    state.create(tmp_path)
    if approve_spec:
        state.approve(tmp_path, "spec")
    set_test_command(tmp_path, f"{MARKER} && exit 1")
    assert run_gate(monkeypatch, tmp_path, commit_event(tmp_path, "docs: add plan")) == 0
    assert not (tmp_path / "ran.marker").exists()


def test_tests_run_in_the_project_directory(proj, monkeypatch):
    set_test_command(proj, "pwd -P > cwd.txt")
    run_gate(monkeypatch, proj, commit_event(proj))
    assert (proj / "cwd.txt").read_text().strip() == str(proj.resolve())


def test_missing_test_runner_blocks_with_reason(proj, monkeypatch, capsys):
    set_test_command(proj, "specforge-no-such-runner-xyz")
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    assert "not found" in capsys.readouterr().err


def test_no_test_command_available_blocks_with_reason(proj, monkeypatch, capsys):
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    assert "test_command" in capsys.readouterr().err


def test_timeout_blocks(proj, monkeypatch, capsys):
    set_test_command(proj, "sleep 5")
    monkeypatch.setattr(test_gate, "TEST_TIMEOUT", 1)
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    assert "timed out" in capsys.readouterr().err


def test_corrupt_state_blocks_commit(tmp_path, monkeypatch, capsys):
    (tmp_path / ".spec").mkdir()
    (tmp_path / ".spec" / "state.json").write_text("{bad")
    assert run_gate(monkeypatch, tmp_path, commit_event(tmp_path)) == 2
    assert "corrupt" in capsys.readouterr().err.lower()


def test_missing_state_blocks_commit(tmp_path, monkeypatch, capsys):
    (tmp_path / ".spec").mkdir()
    assert run_gate(monkeypatch, tmp_path, commit_event(tmp_path)) == 2
    assert "/specforge:spec" in capsys.readouterr().err


def test_corrupt_config_blocks_commit(proj, monkeypatch, capsys):
    (proj / ".spec" / "config.json").write_text("{bad")
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    assert "config" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("raw", ["", "nope", "[]", "null"])
def test_malformed_input_fails_closed(proj, monkeypatch, capsys, raw):
    assert run_gate(monkeypatch, proj, raw) == 2
    assert capsys.readouterr().err


def test_internal_error_fails_closed(proj, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(test_gate, "evaluate", boom)
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    assert "kaboom" in capsys.readouterr().err


# ---------- the gate tests what would be committed, not the working tree ----------

import subprocess as _sp


def _git(proj, *args):
    return _sp.run(["git", *args], cwd=proj, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def gitproj(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    (tmp_path / "README").write_text("x\n")
    _git(tmp_path, "add", "README")
    _git(tmp_path, "commit", "-qm", "init")
    state.create(tmp_path)
    state.approve(tmp_path, "spec")
    state.approve(tmp_path, "plan")
    return tmp_path


def git_commit_event(proj, command="git commit -m 'feat: add thing'"):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(proj)}


def set_config(proj, **cfg):
    (proj / ".spec" / "config.json").write_text(json.dumps(cfg))


def staged(proj):
    return _git(proj, "diff", "--cached", "--name-only").split()


def test_unstaged_file_on_disk_does_not_make_a_commit_pass(gitproj, monkeypatch, capsys):
    set_config(gitproj, test_command="test -f index.html")
    (gitproj / "index.html").write_text("page\n")  # on disk, never staged
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj)) == 2
    assert "tests failed" in capsys.readouterr().err.lower()


def test_unstaged_edit_does_not_make_a_commit_pass(gitproj, monkeypatch):
    set_config(gitproj, test_command="grep -q GOOD app.txt")
    (gitproj / "app.txt").write_text("BAD\n")
    _git(gitproj, "add", "app.txt")
    (gitproj / "app.txt").write_text("GOOD\n")  # fixed on disk only
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj)) == 2


def test_staged_content_that_passes_is_allowed_despite_bad_unstaged_edits(gitproj, monkeypatch):
    set_config(gitproj, test_command="grep -q GOOD app.txt")
    (gitproj / "app.txt").write_text("GOOD\n")
    _git(gitproj, "add", "app.txt")
    (gitproj / "app.txt").write_text("BAD\n")
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj)) == 0


def test_git_add_in_the_same_command_is_taken_into_account(gitproj, monkeypatch):
    set_config(gitproj, test_command="test -f index.html")
    (gitproj / "index.html").write_text("page\n")
    event = git_commit_event(gitproj, "git add index.html && git commit -m 'feat: page'")
    assert run_gate(monkeypatch, gitproj, event) == 0
    assert staged(gitproj) == []  # the real index is untouched by the gate


def test_commit_all_flag_includes_tracked_changes(gitproj, monkeypatch):
    set_config(gitproj, test_command="grep -q GOOD app.txt")
    (gitproj / "app.txt").write_text("BAD\n")
    _git(gitproj, "add", "app.txt")
    _git(gitproj, "commit", "-qm", "test: app")
    (gitproj / "app.txt").write_text("GOOD\n")
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj, "git commit -am 'feat: fix'")) == 0
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj, "git commit -m 'feat: fix'")) == 2


def test_configured_env_links_are_available_to_the_tests(gitproj, monkeypatch):
    set_config(gitproj, test_command="test -f envdir/tool", test_env_links=["envdir"])
    (gitproj / "envdir").mkdir()
    (gitproj / "envdir" / "tool").write_text("")
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj)) == 0


def test_common_env_dirs_are_linked_by_default(gitproj, monkeypatch):
    set_config(gitproj, test_command="test -f node_modules/dep/index.js")
    (gitproj / "node_modules" / "dep").mkdir(parents=True)
    (gitproj / "node_modules" / "dep" / "index.js").write_text("")
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj)) == 0


def test_worktree_scope_keeps_the_old_behaviour(gitproj, monkeypatch):
    set_config(gitproj, test_command="test -f index.html", commit_gate_scope="worktree")
    (gitproj / "index.html").write_text("page\n")
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj)) == 0


@pytest.mark.parametrize("exit_code", [0, 1])
def test_temporary_tree_is_removed_afterwards(gitproj, tmp_path_factory, monkeypatch, exit_code):
    record = tmp_path_factory.mktemp("rec") / "where.txt"
    set_config(gitproj, test_command=f"pwd -P > {record}; exit {exit_code}")
    run_gate(monkeypatch, gitproj, git_commit_event(gitproj))
    ran_in = Path(record.read_text().strip())
    assert ran_in != gitproj.resolve()
    assert not ran_in.exists()


def test_failed_replay_of_git_add_blocks_with_reason(gitproj, monkeypatch, capsys):
    set_config(gitproj, test_command="exit 0")
    event = git_commit_event(gitproj, "git add no-such-file && git commit -m 'feat: x'")
    assert run_gate(monkeypatch, gitproj, event) == 2
    assert "git add" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command,expected",
    [
        ("git commit -am 'feat: x'", True),
        ("git commit -a -m 'feat: x'", True),
        ("git commit --all -m x", True),
        ("git commit -m 'feat: x'", False),
        ("git commit -m '-a looks like a flag'", False),
        ("git commit -m x -a", True),
        ("git commit --amend --no-edit", False),
        ("git commit -m 'feat' && git add -A", False),
        ("git status", False),
    ],
)
def test_commit_all_flag_detection(command, expected):
    assert test_gate.commit_includes_tracked_changes(command) is expected


def test_project_in_a_subfolder_of_the_repo_runs_tests_in_that_subfolder(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    proj = tmp_path / "apps" / "web"
    proj.mkdir(parents=True)
    (proj / "page.html").write_text("x\n")
    _git(tmp_path, "add", "apps/web/page.html")
    state.create(proj)
    state.approve(proj, "spec")
    state.approve(proj, "plan")
    set_config(proj, test_command="test -f page.html && test ! -e apps")
    assert run_gate(monkeypatch, proj, git_commit_event(proj)) == 0


# ---------- per-task test scope: only done tasks and the current task ----------

PLAN = """# Plan: x

## Tasks
- [x] T1 (M1, AC-18): state
- [x] T2 (M1, AC-23): snapshots
- [ ] T3 (M1, AC-24, AC-4): subscribe
- [ ] T4 (M1, AC-1): send
- [ ] T5 (M2, AC-19): page
"""


def write_plan(proj, text=PLAN):
    (proj / ".spec" / "plan.md").write_text(text)


def write_tests(proj, *ids):
    (proj / "tests").mkdir(exist_ok=True)
    (proj / "tests" / "ids.test.js").write_text("\n".join(f'test("{i}: x", () => {{}});' for i in ids))


def test_scope_is_done_tasks_plus_the_first_open_task(tmp_path):
    (tmp_path / ".spec").mkdir()
    write_plan(tmp_path)
    assert test_gate.acs_in_scope(tmp_path) == ["AC-18", "AC-23", "AC-24", "AC-4"]


def test_scope_is_none_when_every_task_is_done(tmp_path):
    (tmp_path / ".spec").mkdir()
    write_plan(tmp_path, PLAN.replace("[ ]", "[x]"))
    assert test_gate.acs_in_scope(tmp_path) is None


def test_scope_is_none_without_a_plan_or_tasks(tmp_path):
    (tmp_path / ".spec").mkdir()
    assert test_gate.acs_in_scope(tmp_path) is None
    write_plan(tmp_path, "# Plan\n\nno tasks here\n")
    assert test_gate.acs_in_scope(tmp_path) is None


def test_ac_regex_matches_whole_ids_only():
    pattern = re.compile(test_gate.ac_regex(["AC-1", "AC-18"]))
    assert pattern.search("AC-1: sends")
    assert pattern.search("AC-18 (a): state")
    assert not pattern.search("AC-10: other")
    assert not pattern.search("AC-181: other")


def test_pytest_k_expression():
    assert test_gate.pytest_k(["AC-1", "AC-18"]) == "ac1_ or ac18_"


def test_filter_runs_only_the_tests_in_scope(proj, monkeypatch):
    write_plan(proj)
    write_tests(proj, "AC-18", "AC-23", "AC-24", "AC-4", "AC-1", "AC-19")
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 1",  # the whole suite still fails: later tasks are not built
        "test_filter": "printf '%s' {ac_regex} > got.txt; exit 0",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 0
    got = (proj / "got.txt").read_text()
    alternatives = set(re.search(r"\(\?:([^)]*)\)", got).group(1).split("|"))
    assert alternatives == {"AC-18", "AC-23", "AC-24", "AC-4"}


def test_filter_with_pytest_placeholder(proj, monkeypatch):
    write_plan(proj)
    write_tests(proj, "AC-18", "AC-23", "AC-24", "AC-4")
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 1",
        "test_filter": "printf '%s' {pytest_k} > got.txt; exit 0",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 0
    assert (proj / "got.txt").read_text() == "ac18_ or ac23_ or ac24_ or ac4_"


def test_filter_failure_still_blocks(proj, monkeypatch, capsys):
    write_plan(proj)
    write_tests(proj, "AC-18", "AC-23", "AC-24", "AC-4")
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 0", "test_filter": "echo SCOPED-FAIL; exit 1 #{ac_regex}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    err = capsys.readouterr().err
    assert "SCOPED-FAIL" in err and "AC-24" in err  # says which ACs were in scope


def test_an_ac_in_scope_with_no_tests_blocks(proj, monkeypatch, capsys):
    write_plan(proj)
    write_tests(proj, "AC-18", "AC-23", "AC-4")  # nothing for AC-24
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 0", "test_filter": "exit 0 #{ac_regex}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    assert "AC-24" in capsys.readouterr().err


def test_pytest_style_test_names_count_as_tests(proj, monkeypatch):
    write_plan(proj)
    (proj / "tests").mkdir()
    (proj / "tests" / "test_x.py").write_text(
        "def test_ac18_a(): pass\ndef test_ac23_b(): pass\ndef test_ac24_c(): pass\ndef test_ac4_d(): pass\n"
    )
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 1", "test_filter": "exit 0 #{pytest_k}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 0


def test_whole_suite_runs_when_every_task_is_done(proj, monkeypatch):
    write_plan(proj, PLAN.replace("[ ]", "[x]"))
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": f"{MARKER} && exit 0", "test_filter": "exit 1 #{ac_regex}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 0
    assert (proj / "ran.marker").exists()


def test_without_a_filter_the_whole_suite_runs(proj, monkeypatch):
    write_plan(proj)
    set_test_command(proj, f"{MARKER} && exit 1")
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    assert (proj / "ran.marker").exists()


# ---------- scope fixes found in the Track C v2 run ----------

def test_ac_mentions_in_docs_inside_test_folders_do_not_count_as_tests(proj, monkeypatch, capsys):
    write_plan(proj)
    write_tests(proj, "AC-18", "AC-23", "AC-4")
    (proj / "tests" / "CLAUDE.md").write_text("Related ACs: AC-18, AC-23, AC-24, AC-4\n")
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 0", "test_filter": "exit 0 #{ac_regex}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    assert "AC-24" in capsys.readouterr().err


SCOPE_PLAN = """## Tasks
- [x] T1 (M1, AC-1): first
- [ ] T2 (M1, AC-2): second
"""


@pytest.fixture
def scoped_gitproj(gitproj):
    (gitproj / ".spec" / "plan.md").write_text(SCOPE_PLAN)
    (gitproj / "tests").mkdir()
    (gitproj / "tests" / "one.test.js").write_text('test("AC-1: works", () => {});\n')
    (gitproj / "src").mkdir()
    (gitproj / "src" / "a.js").write_text("export const a = 1;\n")
    set_config(gitproj, test_command="exit 1",
               test_filter="printf '%s' {ac_regex} > \"$OUT\"; exit 0")
    _git(gitproj, "add", "tests", "src")
    _git(gitproj, "commit", "-qm", "test: T1")
    return gitproj


def test_a_commit_without_code_changes_is_checked_against_done_tasks_only(
    scoped_gitproj, tmp_path_factory, monkeypatch
):
    out = tmp_path_factory.mktemp("o") / "got.txt"
    monkeypatch.setenv("OUT", str(out))
    (scoped_gitproj / "docs").mkdir()
    (scoped_gitproj / "docs" / "notes.md").write_text("spec v2\n")
    _git(scoped_gitproj, "add", "docs")
    assert run_gate(monkeypatch, scoped_gitproj, git_commit_event(scoped_gitproj, "git commit -m 'docs: v2'")) == 0
    assert "AC-1" in out.read_text() and "AC-2" not in out.read_text()


def test_a_code_commit_for_the_current_task_needs_its_tests(scoped_gitproj, monkeypatch, capsys):
    (scoped_gitproj / "src" / "a.js").write_text("export const a = 2;\n")
    _git(scoped_gitproj, "add", "src")
    assert run_gate(monkeypatch, scoped_gitproj, git_commit_event(scoped_gitproj)) == 2
    assert "AC-2" in capsys.readouterr().err


def test_a_code_commit_includes_the_current_task_once_its_tests_exist(
    scoped_gitproj, tmp_path_factory, monkeypatch
):
    out = tmp_path_factory.mktemp("o") / "got.txt"
    monkeypatch.setenv("OUT", str(out))
    (scoped_gitproj / "tests" / "two.test.js").write_text('test("AC-2: works", () => {});\n')
    (scoped_gitproj / "src" / "a.js").write_text("export const a = 2;\n")
    _git(scoped_gitproj, "add", "src", "tests")
    assert run_gate(monkeypatch, scoped_gitproj, git_commit_event(scoped_gitproj)) == 0
    assert "AC-1" in out.read_text() and "AC-2" in out.read_text()


def test_a_non_code_commit_with_no_done_tasks_runs_no_tests(gitproj, monkeypatch):
    (gitproj / ".spec" / "plan.md").write_text("## Tasks\n- [ ] T1 (M1, AC-1): first\n")
    set_config(gitproj, test_command=f"{MARKER}; exit 1",
               test_filter=f"{MARKER}; exit 1 #{{ac_regex}}")
    (gitproj / "notes.md").write_text("x\n")
    _git(gitproj, "add", "notes.md")
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj, "git commit -m 'docs: notes'")) == 0
