import io
import os
import shlex
import shutil
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


@pytest.mark.parametrize("how", ["timeout", "crash"])
def test_temporary_tree_is_removed_after_a_timeout_or_a_crash(gitproj, monkeypatch, how):
    built = []
    real_build = test_gate.build_commit_tree

    def recording_build(*args, **kwargs):
        result = real_build(*args, **kwargs)
        built.append(result[0])
        return result

    monkeypatch.setattr(test_gate, "build_commit_tree", recording_build)
    if how == "timeout":
        monkeypatch.setattr(test_gate, "TEST_TIMEOUT", 1)
        set_config(gitproj, test_command="sleep 5")
    else:
        set_config(gitproj, test_command="exit 0")
        real_run = test_gate.subprocess.run

        def crash_the_test_run(*a, **k):
            if k.get("timeout") == test_gate.TEST_TIMEOUT:
                raise RuntimeError("kaboom")
            return real_run(*a, **k)

        monkeypatch.setattr(test_gate.subprocess, "run", crash_the_test_run)
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj)) == 2
    assert len(built) == 1
    assert not built[0].exists()


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


# `git commit <paths>` commits the copies on disk of the tracked files it names,
# on top of HEAD, and leaves other staged changes out. `-i <paths>` adds those
# copies to everything staged. The gate has to test that, not the index.

@pytest.fixture
def pathproj(gitproj):
    """app.txt is OLD in HEAD, GOOD in the index and BAD on disk."""
    (gitproj / "app.txt").write_text("OLD\n")
    _git(gitproj, "add", "app.txt")
    _git(gitproj, "commit", "-qm", "test: app")
    (gitproj / "app.txt").write_text("GOOD\n")
    _git(gitproj, "add", "app.txt")
    (gitproj / "app.txt").write_text("BAD\n")
    set_config(gitproj, test_command="grep -q GOOD app.txt")
    return gitproj


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'feat: fix' app.txt",
        "git commit -m 'feat: fix' -- app.txt",
        "git commit app.txt -m 'feat: fix'",
        "git commit --only -m 'feat: fix' app.txt",
        "git commit -o -m 'feat: fix' app.txt",
        "git commit -i -m 'feat: fix' app.txt",
        "git commit --include -m 'feat: fix' app.txt",
        "git commit --amend -m 'feat: fix' app.txt",
        "git commit -m 'feat: fix' *.txt",
        "git commit --author 'A <a@example.invalid>' -m 'feat: fix' app.txt",
    ],
)
def test_a_commit_naming_files_is_tested_on_their_copies_on_disk(pathproj, monkeypatch, command):
    # The staged copy passes, but git would commit the failing copy on disk.
    assert run_gate(monkeypatch, pathproj, git_commit_event(pathproj, command)) == 2


def test_a_commit_naming_files_passes_when_their_copies_on_disk_pass(pathproj, monkeypatch):
    (pathproj / "app.txt").write_text("BAD\n")
    _git(pathproj, "add", "app.txt")
    (pathproj / "app.txt").write_text("GOOD\n")
    event = git_commit_event(pathproj, "git commit -m 'feat: fix' app.txt")
    assert run_gate(monkeypatch, pathproj, event) == 0
    assert staged(pathproj) == ["app.txt"]  # the real index is untouched by the gate
    assert _git(pathproj, "show", ":app.txt") == "BAD\n"


@pytest.mark.parametrize(
    "command,expected",
    [
        ("git commit -m 'feat: fix' app.txt", 2),  # other.txt's staged GOOD is left out
        ("git commit -i -m 'feat: fix' app.txt", 0),  # other.txt's staged GOOD is kept
    ],
)
def test_only_leaves_out_other_staged_changes_and_include_keeps_them(
    pathproj, monkeypatch, command, expected
):
    (pathproj / "other.txt").write_text("OLD\n")
    _git(pathproj, "add", "other.txt")
    _git(pathproj, "commit", "-qm", "test: other")
    (pathproj / "other.txt").write_text("GOOD\n")
    _git(pathproj, "add", "other.txt")
    (pathproj / "app.txt").write_text("GOOD\n")
    set_config(pathproj, test_command="grep -q GOOD app.txt && grep -q GOOD other.txt")
    assert run_gate(monkeypatch, pathproj, git_commit_event(pathproj, command)) == expected


def test_a_commit_naming_a_file_deleted_on_disk_leaves_it_out(gitproj, monkeypatch):
    (gitproj / "gone.txt").write_text("x\n")
    _git(gitproj, "add", "gone.txt")
    _git(gitproj, "commit", "-qm", "test: gone")
    (gitproj / "gone.txt").unlink()
    set_config(gitproj, test_command="test ! -e gone.txt")
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj, "git commit -m 'feat: x' gone.txt")) == 0
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj, "git commit -m 'feat: x'")) == 2


def test_a_commit_naming_files_leaves_out_untracked_files_they_match(pathproj, monkeypatch):
    (pathproj / "app.txt").write_text("GOOD\n")
    (pathproj / "new.txt").write_text("new\n")  # untracked: git commit *.txt skips it
    set_config(pathproj, test_command="grep -q GOOD app.txt && test ! -e new.txt")
    event = git_commit_event(pathproj, "git commit -m 'feat: fix' '*.txt'")
    assert run_gate(monkeypatch, pathproj, event) == 0


def test_a_first_commit_naming_files_starts_from_nothing(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "x.txt").write_text("BAD\n")
    (tmp_path / "y.txt").write_text("y\n")
    _git(tmp_path, "add", "x.txt", "y.txt")
    (tmp_path / "x.txt").write_text("GOOD\n")
    state.create(tmp_path)
    state.approve(tmp_path, "spec")
    state.approve(tmp_path, "plan")
    set_config(tmp_path, test_command="grep -q GOOD x.txt && test ! -e y.txt")
    event = git_commit_event(tmp_path, "git commit -m 'feat: x' x.txt")
    assert run_gate(monkeypatch, tmp_path, event) == 0


def test_a_commit_naming_files_in_a_project_subfolder(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    proj = tmp_path / "apps" / "web"
    proj.mkdir(parents=True)
    (proj / "page.html").write_text("BAD\n")
    _git(tmp_path, "add", "apps/web/page.html")
    _git(tmp_path, "commit", "-qm", "init")
    (proj / "page.html").write_text("GOOD\n")
    state.create(proj)
    state.approve(proj, "spec")
    state.approve(proj, "plan")
    set_config(proj, test_command="grep -q GOOD page.html")
    assert run_gate(monkeypatch, proj, git_commit_event(proj, "git commit -m 'feat: x' page.html")) == 0
    assert run_gate(monkeypatch, proj, git_commit_event(proj, "git commit -m 'feat: x'")) == 2


@pytest.mark.parametrize(
    "command,needle",
    [
        ("git commit -m 'feat: x' no-such-file", "no-such-file"),
        ("git commit -a -m 'feat: x' app.txt", "-a"),
        ("git commit --pathspec-from-file=list.txt -m 'feat: x'", "--pathspec-from-file"),
    ],
)
def test_path_commits_the_gate_cannot_model_are_blocked_with_a_reason(
    pathproj, monkeypatch, capsys, command, needle
):
    set_config(pathproj, test_command="exit 0")
    assert run_gate(monkeypatch, pathproj, git_commit_event(pathproj, command)) == 2
    assert needle in capsys.readouterr().err


@pytest.mark.parametrize(
    "command,paths",
    [
        ("git commit -m 'feat: x'", []),
        ("git commit -m 'feat: x' app.txt src/b.js", ["app.txt", "src/b.js"]),
        ("git commit -m x -- -odd-name", ["-odd-name"]),
        ("git commit -am x", []),
        ("git commit -mx a.txt", ["a.txt"]),
        ("git commit --message=x a.txt", ["a.txt"]),
        ("git commit --message x a.txt", ["a.txt"]),
        ("git commit -C HEAD a.txt", ["a.txt"]),
        ("git commit --author=A --date now -m x a.txt", ["a.txt"]),
        ("git commit -uno -m x", []),
        ("git commit -m x 2>&1", []),
        ("git commit -m x > log.txt", []),
        ("git commit -m x && git push origin main", []),
        ("git commit -m x; git log a.txt", []),
        ("git commit -m x | tail -1", []),
        ("git commit -F - <<'EOF'\nfeat: x\nbody line\nEOF", []),
        ("git commit -m \"$(cat <<'EOF'\nfeat: x\n\nbody\nEOF\n)\"", []),
        ("git commit -m \"$(cat <<'EOF'\nfeat: x\nEOF\n)\" a.txt", ["a.txt"]),
        ("git add a.txt && git commit -m x b.txt", ["b.txt"]),
        ("git -C sub commit -m x a.txt", ["a.txt"]),
    ],
)
def test_commit_paths_are_read_from_the_command(command, paths):
    assert test_gate.commit_args(command)["paths"] == paths


@pytest.mark.parametrize(
    "command,mode",
    [
        ("git commit -m x a.txt", "only"),
        ("git commit -o -m x a.txt", "only"),
        ("git commit -i -m x a.txt", "include"),
        ("git commit --include -m x a.txt", "include"),
        ("git commit -im x a.txt", "include"),
        ("git commit -uno -m x a.txt", "only"),  # -u takes "no"; it is not -o
    ],
)
def test_commit_path_mode_is_read_from_the_command(command, mode):
    assert test_gate.commit_args(command)["mode"] == mode


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
    assert test_gate.pytest_k(["AC-1", "AC-18"]) == "test_ac1_ or test_ac18_"


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
    assert (proj / "got.txt").read_text() == "test_ac18_ or test_ac23_ or test_ac24_ or test_ac4_"


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


# ---------- a filter that ran no tests is not a pass ----------
# Real output from each runner (Node 24, Jest 29, Vitest 2, Mocha 10) when the
# filter matched nothing, and when it matched something.

NODE_TAP_NONE = """TAP version 13
1..0
# Subtest: tests/b.test.js
ok 1 - tests/b.test.js
  ---
  duration_ms: 77.2
  ...
1..1
# tests 1
# suites 0
# pass 1
# fail 0
"""
NODE_SPEC_NONE = """✔ tests/b.test.js (77.282285ms)
ℹ tests 1
ℹ suites 0
ℹ pass 1
ℹ fail 0
"""
NODE_TAP_NESTED = """TAP version 13
# Subtest: Other
    # Subtest: AC-3: nested
    ok 1 - AC-3: nested
    1..1
ok 1 - Other
1..1
# tests 1
# pass 1
"""
NODE_SPEC_SUITE = """▶ AC-2 group
  ✔ works (0.713826ms)
✔ AC-2 group (1.977701ms)
ℹ tests 1
ℹ pass 1
"""
JEST_NONE = """Test Suites: 1 skipped, 0 of 1 total
Tests:       2 skipped, 2 total
Snapshots:   0 total
"""
JEST_SOME = """PASS tests/a.test.js
Tests:       1 skipped, 1 passed, 2 total
"""
VITEST_NONE = """ ↓ tests/v.test.mjs (2 tests | 2 skipped)
 Test Files  1 skipped (1)
      Tests  2 skipped (2)
"""
VITEST_SOME = """ ✓ tests/v.test.mjs (2 tests | 1 skipped) 3ms
 Test Files  1 passed (1)
      Tests  1 passed | 1 skipped (2)
"""
MOCHA_NONE = "\n\n  0 passing (1ms)\n\n"
MOCHA_SOME = "\n  Controller\n    ✔ AC-1: works\n\n  1 passing (3ms)\n"


@pytest.mark.parametrize(
    "output,expected",
    [
        (NODE_TAP_NONE, False),
        (NODE_SPEC_NONE, False),
        (NODE_TAP_NESTED, True),
        (NODE_SPEC_SUITE, True),
        ("TAP version 13\nok 1 - AC-1: works\n# tests 1\n", True),
        (JEST_NONE, False),
        (JEST_SOME, True),
        ("\x1b[1mTests:\x1b[22m       \x1b[33m2 skipped\x1b[39m, 2 total\n", False),
        (VITEST_NONE, False),
        (VITEST_SOME, True),
        (MOCHA_NONE, False),
        (MOCHA_SOME, True),
        ("No tests found, exiting with code 1\nRun with `--passWithNoTests` to exit with code 0\n", False),
        ("No test files found, exiting with code 1\n", False),  # vitest
        ("", None),  # no runner recognised: nothing to judge by
        ("all good\n", None),
        ("....\n4 passed in 0.10s\n", None),  # pytest: exit 5 already blocks an empty selection
    ],
)
def test_tests_ran_reads_the_runner_output(output, expected):
    assert test_gate.tests_ran(output) is expected


@pytest.mark.parametrize("output", [NODE_TAP_NONE, JEST_NONE, VITEST_NONE, MOCHA_NONE])
def test_a_filtered_run_that_ran_no_tests_blocks(proj, monkeypatch, capsys, tmp_path_factory, output):
    write_plan(proj)
    write_tests(proj, "AC-18", "AC-23", "AC-24", "AC-4")
    out = tmp_path_factory.mktemp("out") / "out.txt"
    out.write_text(output)
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 0", "test_filter": f"cat {out} #{{ac_regex}}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    err = capsys.readouterr().err
    assert "ran no tests" in err and "AC-24" in err


def test_a_filtered_run_that_ran_tests_is_allowed(proj, monkeypatch, tmp_path_factory):
    write_plan(proj)
    write_tests(proj, "AC-18", "AC-23", "AC-24", "AC-4")
    out = tmp_path_factory.mktemp("out") / "out.txt"
    out.write_text(JEST_SOME)
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 0", "test_filter": f"cat {out} #{{ac_regex}}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 0


def test_the_whole_suite_is_not_judged_by_its_output(proj, monkeypatch, tmp_path_factory):
    # Without a filter the gate keeps upstream's behaviour: the exit code decides.
    out = tmp_path_factory.mktemp("out") / "out.txt"
    out.write_text(NODE_TAP_NONE)
    (proj / ".spec" / "config.json").write_text(json.dumps({"test_command": f"cat {out}"}))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 0


NODE_AC1 = """const test = require("node:test");
const assert = require("node:assert");
test("AC-1: works", () => assert.ok(require("../src/a.js").ok));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize(
    "test_file,ok_code,expected",
    [
        ("a.test.js", False, 2),  # control: the AC-1 test runs and fails
        ("a.test.js", True, 0),  # control: the AC-1 test runs and passes
        ("a_cases.js", True, 2),  # node --test doesn't load this file: nothing ran
    ],
)
def test_node_filter_end_to_end(proj, monkeypatch, capsys, test_file, ok_code, expected):
    write_plan(proj, "- [ ] T1 do it (AC-1)\n- [ ] T2 more (AC-2)\n")
    (proj / "tests").mkdir()
    (proj / "tests" / test_file).write_text(NODE_AC1)
    (proj / "src").mkdir()
    (proj / "src" / "a.js").write_text(f"module.exports = {{ ok: {str(ok_code).lower()} }};\n")
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "node --test", "test_filter": "node --test --test-name-pattern {ac_regex}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == expected
    if test_file == "a_cases.js":
        assert "ran no tests" in capsys.readouterr().err


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


# ---------- scope fixes found in Track C Stage 10 ----------

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
    state.record_red(scoped_gitproj, "T2", "abc1234")  # M4: T2's tests were seen failing
    assert run_gate(monkeypatch, scoped_gitproj, git_commit_event(scoped_gitproj)) == 0
    assert "AC-1" in out.read_text() and "AC-2" in out.read_text()


def test_a_non_code_commit_with_no_done_tasks_runs_no_tests(gitproj, monkeypatch):
    (gitproj / ".spec" / "plan.md").write_text("## Tasks\n- [ ] T1 (M1, AC-1): first\n")
    set_config(gitproj, test_command=f"{MARKER}; exit 1",
               test_filter=f"{MARKER}; exit 1 #{{ac_regex}}")
    (gitproj / "notes.md").write_text("x\n")
    _git(gitproj, "add", "notes.md")
    assert run_gate(monkeypatch, gitproj, git_commit_event(gitproj, "git commit -m 'docs: notes'")) == 0


def test_docs_inside_code_folders_are_not_code_changes(scoped_gitproj, tmp_path_factory, monkeypatch):
    out = tmp_path_factory.mktemp("o") / "got.txt"
    monkeypatch.setenv("OUT", str(out))
    (scoped_gitproj / "src" / "CLAUDE.md").write_text("Related ACs: AC-1, AC-2\n")
    _git(scoped_gitproj, "add", "src/CLAUDE.md")
    assert run_gate(monkeypatch, scoped_gitproj, git_commit_event(scoped_gitproj, "git commit -m 'docs: guide'")) == 0
    assert "AC-2" not in out.read_text()


@pytest.mark.parametrize(
    "paths,expected",
    [
        (["src/a.js"], True),
        (["apps/web/src/a.js"], True),
        (["src/CLAUDE.md"], False),
        (["src/tests/a.test.js"], False),
        (["docs/x.md", ".spec/plan.md"], False),
        (["README.md", "lib/util.py"], True),
    ],
)
def test_touches_code(paths, expected):
    cfg = {"code_dirs": ["src", "app", "lib"], "test_dirs": ["tests"]}
    assert test_gate.touches_code(paths, cfg) is expected


# ---------- a test belongs to the AC its name starts with (Track C v2 re-run) ----------
# A test named "AC-9: sends as in AC-2" is an AC-9 test. Matching AC-2 anywhere
# in the name pulled it into T2's scope, so later tasks were green before their turn.

def test_ac_regex_matches_an_id_only_at_the_start_of_a_name():
    pattern = re.compile(test_gate.ac_regex(["AC-2", "AC-18"]))
    assert pattern.search("AC-2: sends")
    assert pattern.search("AC-18 (a): state")
    assert not pattern.search("AC-9: sends the message as in AC-2")
    assert not pattern.search("AC-15: after a failure, a new message is sent as in AC-18")
    assert not pattern.search("rejects bad input (AC-2)")
    assert not pattern.search("AC-20: other")


def test_pytest_k_does_not_match_a_mention_inside_another_tests_name():
    expr = test_gate.pytest_k(["AC-2"])
    # pytest -k is a case-insensitive substring match on the test's name
    assert expr.lower() in "test_ac2_sends"
    assert expr.lower() not in "test_ac9_sends_as_in_ac2_reply"


def test_a_mention_of_an_ac_inside_another_test_is_not_a_test_for_it(proj, monkeypatch, capsys):
    write_plan(proj)
    (proj / "tests").mkdir()
    (proj / "tests" / "x.test.js").write_text(
        'test("AC-18: x", () => {});\n'
        'test(`AC-23: x`, () => {});\n'
        "test('AC-4: x', () => {});\n"
        'test("AC-23: behaves as in AC-24", () => {});\n'
        "// AC-24 is covered elsewhere\n"
    )
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 0", "test_filter": "exit 0 #{ac_regex}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    assert "AC-24" in capsys.readouterr().err


def test_a_pytest_name_mentioning_another_ac_is_not_a_test_for_it(proj, monkeypatch, capsys):
    write_plan(proj)
    (proj / "tests").mkdir()
    (proj / "tests" / "test_x.py").write_text(
        "def test_ac18_a(): pass\ndef test_ac23_like_ac24_b(): pass\ndef test_ac4_d(): pass\n"
    )
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 0", "test_filter": "exit 0 #{pytest_k}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    assert "AC-24" in capsys.readouterr().err


def test_a_template_literal_name_counts_as_a_test(proj, monkeypatch):
    write_plan(proj)
    (proj / "tests").mkdir()
    (proj / "tests" / "x.test.js").write_text(
        "".join(f"test(`{i}: x`, () => {{}});\n" for i in ("AC-18", "AC-23", "AC-24", "AC-4"))
    )
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 1", "test_filter": "exit 0 #{ac_regex}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 0


NODE_NAMES_TEST = """
import { test, describe } from 'node:test';
import { appendFileSync } from 'node:fs';
const ran = (name) => () => appendFileSync(process.env.RAN, name + '\\n');
test('AC-2: top level', ran('AC-2: top level'));
test('AC-9: sends as in AC-2', ran('AC-9: sends as in AC-2'));
test('AC-20: twenty', ran('AC-20: twenty'));
test('rejects bad input (AC-2)', ran('rejects bad input (AC-2)'));
describe('controller', () => {
  test('AC-2: nested in a suite', ran('AC-2: nested in a suite'));
  test('AC-9: nested, as in AC-2', ran('AC-9: nested, as in AC-2'));
});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_runs_only_tests_whose_names_start_with_the_ids(tmp_path):
    (tmp_path / "names.test.mjs").write_text(NODE_NAMES_TEST)
    ran = tmp_path / "ran.txt"
    command = "node --test --test-name-pattern " + shlex.quote(test_gate.ac_regex(["AC-2"]))
    _sp.run(command, shell=True, cwd=tmp_path, env={**os.environ, "RAN": str(ran)},
            capture_output=True, timeout=60)
    assert sorted(ran.read_text().splitlines()) == ["AC-2: nested in a suite", "AC-2: top level"]


# ---------- a task's own ACs (M4-AC1) ----------

@pytest.mark.parametrize(
    "line,acs",
    [
        ("- [ ] T1 (M1, AC-1, AC-19): controller", ["AC-1", "AC-19"]),
        ("- [x] T13 (M4, AC-16): `clearChat()` resets to the AC-1 state", ["AC-16"]),
        ("- [ ] T16 (M5, manual check in Scope): page, see AC-3", []),
        ("- [ ] T5 do AC-5 and AC-6", ["AC-5", "AC-6"]),  # no parentheses: every AC-ID counts
        ("- [ ] T6: setup", []),
    ],
)
def test_a_task_owns_the_ac_ids_in_the_parentheses_after_its_id(tmp_path, line, acs):
    (tmp_path / ".spec").mkdir()
    write_plan(tmp_path, f"## Tasks\n{line}\n")
    assert [t[2] for t in test_gate.plan_tasks(tmp_path)] == [acs]


def test_plan_tasks_lists_id_done_and_acs(tmp_path):
    (tmp_path / ".spec").mkdir()
    write_plan(tmp_path)
    tasks = test_gate.plan_tasks(tmp_path)
    assert tasks[:3] == [("T1", True, ["AC-18"]), ("T2", True, ["AC-23"]), ("T3", False, ["AC-24", "AC-4"])]


def test_scope_leaves_out_acs_a_task_only_mentions(tmp_path):
    (tmp_path / ".spec").mkdir()
    write_plan(tmp_path, "## Tasks\n- [x] T1 (M1, AC-1): first\n- [ ] T2 (M1, AC-2): sends as in AC-9\n")
    assert test_gate.acs_in_scope(tmp_path) == ["AC-1", "AC-2"]


def test_current_task_is_the_first_open_one(tmp_path):
    (tmp_path / ".spec").mkdir()
    write_plan(tmp_path)
    assert test_gate.current_task(tmp_path) == ("T3", ["AC-24", "AC-4"])
    write_plan(tmp_path, PLAN.replace("[ ]", "[x]"))
    assert test_gate.current_task(tmp_path) is None
    (tmp_path / ".spec" / "plan.md").unlink()
    assert test_gate.current_task(tmp_path) is None


# ---------- the commit gate wants RED before a task's code (M4-AC6) ----------

@pytest.fixture
def red_gitproj(scoped_gitproj, tmp_path_factory, monkeypatch):
    """T2 (AC-2) is current, its test exists, and a code change is staged."""
    monkeypatch.setenv("OUT", str(tmp_path_factory.mktemp("o") / "got.txt"))
    (scoped_gitproj / "tests" / "two.test.js").write_text('test("AC-2: works", () => {});\n')
    _git(scoped_gitproj, "add", "tests")
    _git(scoped_gitproj, "commit", "-qm", "test: T2")
    (scoped_gitproj / "src" / "a.js").write_text("export const a = 2;\n")
    _git(scoped_gitproj, "add", "src")
    return scoped_gitproj


def test_a_code_commit_without_red_for_the_current_task_is_blocked(red_gitproj, monkeypatch, capsys):
    assert run_gate(monkeypatch, red_gitproj, git_commit_event(red_gitproj)) == 2
    err = capsys.readouterr().err
    assert "T2" in err and "no failing test recorded" in err
    assert "`specforge red`" in err and "python3" not in err  # M13: the command a narrow allow rule permits


def test_a_code_commit_with_red_for_the_current_task_is_allowed(red_gitproj, monkeypatch):
    state.record_red(red_gitproj, "T2", "abc1234")
    assert run_gate(monkeypatch, red_gitproj, git_commit_event(red_gitproj)) == 0


def test_red_for_another_task_does_not_count(red_gitproj, monkeypatch):
    state.record_red(red_gitproj, "T1", "abc1234")
    assert run_gate(monkeypatch, red_gitproj, git_commit_event(red_gitproj)) == 2


def test_red_first_false_turns_the_commit_check_off(red_gitproj, monkeypatch):
    set_config(red_gitproj, test_command="exit 1", red_first=False,
               test_filter="printf '%s' {ac_regex} > \"$OUT\"; exit 0")
    assert run_gate(monkeypatch, red_gitproj, git_commit_event(red_gitproj)) == 0


def test_without_a_test_filter_there_is_no_red_check(red_gitproj, monkeypatch):
    set_config(red_gitproj, test_command="exit 0")
    assert run_gate(monkeypatch, red_gitproj, git_commit_event(red_gitproj)) == 0


def test_a_commit_without_code_changes_needs_no_red(red_gitproj, monkeypatch):
    _git(red_gitproj, "reset", "-q", "src")
    (red_gitproj / "notes.md").write_text("x\n")
    _git(red_gitproj, "add", "notes.md")
    assert run_gate(monkeypatch, red_gitproj, git_commit_event(red_gitproj, "git commit -m 'docs: x'")) == 0


def test_a_test_commit_needs_no_red(red_gitproj, monkeypatch):
    assert run_gate(monkeypatch, red_gitproj, git_commit_event(red_gitproj, "git commit -m 'test: more'")) == 0


def test_a_current_task_without_ac_ids_needs_no_red(red_gitproj, monkeypatch):
    write_plan(red_gitproj, "## Tasks\n- [x] T1 (M1, AC-1): first\n- [ ] T2 (M1): set up the build\n")
    assert run_gate(monkeypatch, red_gitproj, git_commit_event(red_gitproj)) == 0


# ---------- withdrawn approvals keep code commits blocked (M6-AC4) ----------

@pytest.fixture
def withdrawn(gitproj):
    """Mid-build, the user has withdrawn the plan approval. Tests would fail."""
    # absolute: in commit scope the tests run in a temporary copy
    set_config(gitproj, test_command=f"touch '{gitproj}/ran.marker' && exit 1")
    (gitproj / "src").mkdir()
    (gitproj / "src" / "a.py").write_text("x = 1\n")
    _git(gitproj, "add", "src")
    _git(gitproj, "commit", "-qm", "feat: a")
    state.update(gitproj, plan_approved=False)
    return gitproj


def gate(monkeypatch, proj, command):
    return run_gate(monkeypatch, proj, git_commit_event(proj, command))


@pytest.mark.parametrize("prefix", ["feat", "test", "fix"])
def test_a_code_commit_without_the_plan_approval_is_blocked(withdrawn, monkeypatch, capsys, prefix):
    (withdrawn / "src" / "a.py").write_text("x = 2\n")
    _git(withdrawn, "add", "src")
    assert gate(monkeypatch, withdrawn, f"git commit -m '{prefix}: change a'") == 2
    err = capsys.readouterr().err
    assert "src/a.py" in err and "/specforge:approve plan" in err
    assert not (withdrawn / "ran.marker").exists()  # no point running the tests


def test_without_the_spec_approval_the_reason_names_the_spec(withdrawn, monkeypatch, capsys):
    state.update(withdrawn, spec_approved=False)
    (withdrawn / "src" / "b.py").write_text("y = 1\n")
    _git(withdrawn, "add", "src")
    assert gate(monkeypatch, withdrawn, "git commit -m 'feat: b'") == 2
    assert "/specforge:approve spec" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [
        "git add src/a.py && git commit -m 'feat: change a'",
        "git commit -am 'feat: change a'",
        "git commit src/a.py -m 'feat: change a'",
    ],
)
def test_code_changes_are_found_however_they_are_committed(withdrawn, monkeypatch, command):
    (withdrawn / "src" / "a.py").write_text("x = 3\n")
    assert gate(monkeypatch, withdrawn, command) == 2


def test_worktree_scope_still_blocks_code_commits(withdrawn, monkeypatch):
    set_config(withdrawn, test_command="exit 0", commit_gate_scope="worktree")
    (withdrawn / "src" / "a.py").write_text("x = 4\n")
    _git(withdrawn, "add", "src")
    assert gate(monkeypatch, withdrawn, "git commit -m 'feat: change a'") == 2


@pytest.mark.parametrize(
    "path",
    ["tests/test_a.py", "docs/notes.md", "README", ".spec/plan.md", "src/CLAUDE.md"],
)
def test_commits_without_code_still_go_through(withdrawn, monkeypatch, path):
    (withdrawn / path).parent.mkdir(parents=True, exist_ok=True)
    (withdrawn / path).write_text("new\n")
    _git(withdrawn, "add", "-f", path)
    assert gate(monkeypatch, withdrawn, "git commit -m 'test: plan more'") == 0
    assert not (withdrawn / "ran.marker").exists()


def test_before_any_approval_the_plan_phase_can_commit_tests(gitproj, monkeypatch):
    state.update(gitproj, spec_approved=False, plan_approved=False)
    set_config(gitproj, test_command="exit 1")
    (gitproj / "tests").mkdir()
    (gitproj / "tests" / "test_a.py").write_text("def test_ac1_x(): assert False\n")
    _git(gitproj, "add", "tests")
    assert gate(monkeypatch, gitproj, "git commit -m 'test: failing tests'") == 0


def test_an_invalid_config_blocks_while_approvals_are_missing(withdrawn, monkeypatch, capsys):
    (withdrawn / ".spec" / "config.json").write_text("{bad")
    (withdrawn / "README").write_text("y\n")
    _git(withdrawn, "add", "README")
    assert gate(monkeypatch, withdrawn, "git commit -m 'docs: x'") == 2
    assert "config.json" in capsys.readouterr().err


def test_reapproving_turns_the_test_run_back_on(withdrawn, monkeypatch):
    state.approve(withdrawn, "plan")
    (withdrawn / "src" / "a.py").write_text("x = 5\n")
    _git(withdrawn, "add", "src")
    assert gate(monkeypatch, withdrawn, "git commit -m 'feat: change a'") == 2
    assert (withdrawn / "ran.marker").exists()  # blocked by the failing tests this time


# ---------- M15: tests outside the test folders ----------

def test_tests_for_finds_an_acs_tests_inside_and_outside_the_test_folders(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.test.js").write_text('test("AC-1: x", () => {});\n')
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "b.test.js").write_text('test("AC-1: y", () => {});\ntest("AC-2: z", () => {});\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.test.js").write_text('test("AC-2: w", () => {});\n')
    assert test_gate.tests_for(tmp_path, ["tests"], "AC-1") == (["tests/a.test.js"], ["test/b.test.js"])
    assert test_gate.tests_for(tmp_path, ["tests"], "AC-2") == ([], ["src/c.test.js", "test/b.test.js"])


def test_tests_for_never_looks_in_the_spec_folder(tmp_path):
    # .spec/holdout/ holds the hidden tests; a message must not point a build session at them
    (tmp_path / ".spec" / "holdout").mkdir(parents=True)
    (tmp_path / ".spec" / "holdout" / "h.test.js").write_text('test("AC-1: hidden", () => {});\n')
    assert test_gate.tests_for(tmp_path, ["tests"], "AC-1") == ([], [])


def test_a_missing_test_message_names_the_folders_and_where_it_was_found(proj, monkeypatch, capsys):
    write_plan(proj)
    write_tests(proj, "AC-18", "AC-23", "AC-4")
    (proj / "test").mkdir()
    (proj / "test" / "x.test.js").write_text('test("AC-24: x", () => {});\n')
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 0", "test_filter": "exit 0 #{ac_regex}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    err = capsys.readouterr().err
    assert "AC-24" in err and "tests/" in err and "test/x.test.js" in err
    assert "test_dirs" in err and ".spec/config.json" in err


# ---------- M16: a test file that failed to load ----------
# Real output, trimmed of stack frames: Node 24 (spec and tap) and pytest 9.

NODE_SPEC_LOAD = """✔ AC-1 slug (1.270904ms)
node:internal/modules/esm/resolve:272
    throw new ERR_MODULE_NOT_FOUND(
          ^

Error [ERR_MODULE_NOT_FOUND]: Cannot find module '/work/proj/src/truncate.js' imported from /work/proj/tests/truncate.test.js
    at finalizeResolution (node:internal/modules/esm/resolve:272:11) {
  code: 'ERR_MODULE_NOT_FOUND',
  url: 'file:///work/proj/src/truncate.js'
}

Node.js v24.21.0
✖ tests/truncate.test.js (52.334057ms)
ℹ tests 2
ℹ suites 0
ℹ pass 1
ℹ fail 1

✖ failing tests:

test at tests/truncate.test.js:1:1
✖ tests/truncate.test.js (52.334057ms)
  'test failed'
"""

NODE_TAP_LOAD = """TAP version 13
# Subtest: AC-1 slug
ok 1 - AC-1 slug
  ---
  duration_ms: 1.969742
  ...
# Error [ERR_MODULE_NOT_FOUND]: Cannot find module '/work/proj/src/truncate.js' imported from /work/proj/tests/truncate.test.js
#     at finalizeResolution (node:internal/modules/esm/resolve:272:11)
# Node.js v24.21.0
# Subtest: tests/truncate.test.js
not ok 2 - tests/truncate.test.js
  ---
  failureType: 'testCodeFailure'
  error: 'test failed'
  ...
1..2
# tests 2
# pass 1
# fail 1
"""

NODE_SPEC_EXPORT = """file:///work/proj/tests/strtools.test.js:3
import { slugify, truncate } from '../src/strtools.js';
                  ^^^^^^^^
SyntaxError: The requested module '../src/strtools.js' does not provide an export named 'truncate'

Node.js v24.21.0
✖ tests/strtools.test.js (49.287706ms)
ℹ tests 1
ℹ pass 0
ℹ fail 1
"""

PYTEST_COLLECT = """
==================================== ERRORS ====================================
___________________ ERROR collecting tests/test_strtools.py ____________________
ImportError while importing test module '/work/proj/tests/test_strtools.py'.
Traceback:
tests/test_strtools.py:1: in <module>
    from src.strtools import slugify, truncate
E   ModuleNotFoundError: No module named 'src.strtools'
=========================== short test summary info ============================
ERROR tests/test_strtools.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.10s
"""

NODE_SPEC_AC_FAIL = """✖ AC-1 slug (2.1ms)
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal
ℹ tests 1
ℹ pass 0
ℹ fail 1

✖ failing tests:

test at tests/strtools.test.js:6:1
✖ AC-1 slug (2.1ms)
"""


@pytest.mark.parametrize("output,file,error", [
    (NODE_SPEC_LOAD, "tests/truncate.test.js", "Cannot find module '/work/proj/src/truncate.js'"),
    (NODE_TAP_LOAD, "tests/truncate.test.js", "Cannot find module '/work/proj/src/truncate.js'"),
    (NODE_SPEC_EXPORT, "tests/strtools.test.js", "does not provide an export named 'truncate'"),
    (PYTEST_COLLECT, "tests/test_strtools.py", "No module named 'src.strtools'"),
])
def test_load_failures_names_the_file_and_the_error(output, file, error):
    got = test_gate.load_failures(output)
    assert [f for f, _ in got] == [file]
    assert error in got[0][1]


@pytest.mark.parametrize("output", [NODE_SPEC_AC_FAIL, NODE_TAP_NONE, NODE_SPEC_NONE, ""])
def test_a_failing_test_or_an_empty_run_is_not_a_load_failure(output):
    assert test_gate.load_failures(output) == []


def test_a_commit_blocked_by_a_file_that_cannot_load_says_so(proj, monkeypatch, capsys, tmp_path_factory):
    write_plan(proj)
    write_tests(proj, "AC-18", "AC-23", "AC-24", "AC-4")
    out = tmp_path_factory.mktemp("o") / "out.txt"
    out.write_text(NODE_SPEC_LOAD)
    (proj / ".spec" / "config.json").write_text(json.dumps({
        "test_command": "exit 0", "test_filter": f"cat '{out}'; exit 1 #{{ac_regex}}",
    }))
    assert run_gate(monkeypatch, proj, commit_event(proj)) == 2
    err = capsys.readouterr().err
    assert "tests/truncate.test.js" in err and "failed to load" in err
    assert "inside each test" in err and "tell the user" in err.lower()
