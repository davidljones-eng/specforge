import io
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
