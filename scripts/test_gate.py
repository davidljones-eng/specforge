#!/usr/bin/env python3
"""PreToolUse hook script: block `git commit` while the project's tests fail.

Commits whose message starts with `test:` are let through so that failing tests
(the RED step of TDD) can be committed. The gate only applies once both the spec
and the plan are approved. Exit 0 allows, exit 2 blocks with a reason on stderr.

By default (commit_gate_scope "commit") the suite runs on exactly what would be
committed: the staged files, plus any `git add ...` in the same command replayed
into a temporary index, plus tracked changes for `commit -a`. That copy lives in
a temporary directory that is always removed. Untracked files on disk are not
included, so they cannot make a commit pass. "worktree" keeps the original
behaviour of running the suite in the project directory.
"""
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import config
import state

TEST_TIMEOUT = 300  # seconds
_TAIL_LINES = 40
_TAIL_CHARS = 4000

_COMMIT = re.compile(r"\bgit(?:\s+-\w+\s+\S+|\s+--\S+)*\s+commit(?![\w-])")
_MESSAGE = re.compile(
    r"""(?:^|\s)(?:-[a-zA-Z]*m|--message)(?:\s+|=)["']?"""
    r"""(?:\$\(\s*cat\s*<<-?\s*(['"]?)\w+\1\s*)?(?P<msg>.*)""",
    re.DOTALL,
)
_TEST_PREFIX = re.compile(r"^test(\([^)]*\))?!?:")
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\n")
_GIT_ADD = re.compile(r"^git(?:\s+-[cC]\s+\S+)*\s+add(?![\w-])")
_COMMIT_OPTS_WITH_VALUE = set("mFcCt")  # short options whose value is the next token


class CommitTreeError(Exception):
    """Could not work out what the commit would contain."""


def is_commit(command: str) -> bool:
    return bool(_COMMIT.search(command))


def only_test_commits(command: str) -> bool:
    """True if the command has git commits and every one is a `test:` commit."""
    matches = list(_COMMIT.finditer(command))
    if not matches:
        return False
    ends = [m.start() for m in matches[1:]] + [len(command)]
    for match, end in zip(matches, ends):
        found = _MESSAGE.search(command[match.end():end])
        if not found or not _TEST_PREFIX.match(found.group("msg").strip()):
            return False
    return True


def _has_pytest_markers(project: Path) -> bool:
    if (project / "pytest.ini").is_file() or (project / "conftest.py").is_file():
        return True
    for name, needle in (("pyproject.toml", "[tool.pytest"), ("tox.ini", "[pytest]"),
                         ("setup.cfg", "[tool:pytest]")):
        path = project / name
        if path.is_file() and needle in path.read_text(errors="replace"):
            return True
    tests = project / "tests"
    return tests.is_dir() and any(
        p.name.startswith("test_") or p.name.endswith("_test.py") for p in tests.rglob("*.py")
    )


def _has_npm_test(project: Path) -> bool:
    path = project / "package.json"
    if not path.is_file():
        return False
    try:
        scripts = json.loads(path.read_text()).get("scripts")
    except (ValueError, AttributeError):
        return False
    return isinstance(scripts, dict) and bool(scripts.get("test"))


def detect_test_command(project):
    project = Path(project)
    if _has_pytest_markers(project):
        return "pytest -q"
    if _has_npm_test(project):
        return "npm test"
    if (project / "pubspec.yaml").is_file():
        return "flutter test"
    return None


def resolve_command(project, cfg, which=shutil.which):
    """Return (command, None) or (None, reason)."""
    if cfg.get("test_command"):
        return cfg["test_command"], None
    command = detect_test_command(project)
    if command is None:
        return None, (
            "no test command found. Set \"test_command\" in .spec/config.json "
            "(for example {\"test_command\": \"make test\"})."
        )
    runner = command.split()[0]
    if not which(runner):
        return None, (
            f"test runner '{runner}' not found on PATH. Install it, or set "
            "\"test_command\" in .spec/config.json."
        )
    return command, None


def _git(cwd, *args, env=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, errors="replace"
    )


def is_git_repo(project) -> bool:
    return _git(project, "rev-parse", "--git-dir").returncode == 0


def _segments_before_first_commit(command: str):
    match = _COMMIT.search(command)
    before = command[: match.start()] if match else command
    return [seg.strip() for seg in _SEGMENT_SPLIT.split(before) if seg.strip()]


def commit_includes_tracked_changes(command: str) -> bool:
    """True if the first `git commit` in the command uses -a / --all."""
    match = _COMMIT.search(command)
    if not match:
        return False
    rest = _SEGMENT_SPLIT.split(command[match.end():])[0]
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    skip = False
    for tok in tokens:
        if skip:
            skip = False
            continue
        if tok == "--all":
            return True
        if tok.startswith("--"):
            continue
        if tok.startswith("-") and len(tok) > 1 and tok[1:].isalpha():
            letters = tok[1:]
            for i, letter in enumerate(letters):
                if letter == "a":
                    return True
                if letter in _COMMIT_OPTS_WITH_VALUE:
                    skip = i == len(letters) - 1  # value is the next token
                    break
    return False


def build_commit_tree(project: Path, command: str, links):
    """Export what `command` would commit into a temporary directory.

    Returns (temp_root, run_dir). The caller must remove temp_root.
    """
    temp_root = Path(tempfile.mkdtemp(prefix="specforge-gate-"))
    try:
        top = _git(project, "rev-parse", "--show-toplevel")
        prefix = _git(project, "rev-parse", "--show-prefix")
        index_path = _git(project, "rev-parse", "--git-path", "index")
        if top.returncode or prefix.returncode or index_path.returncode:
            raise CommitTreeError("could not read the git repository to see what would be committed")
        repo_top = Path(top.stdout.strip())
        real_index = Path(index_path.stdout.strip())
        if not real_index.is_absolute():
            real_index = (project / real_index).resolve()

        temp_index = temp_root / "index"
        if real_index.is_file():
            shutil.copy2(real_index, temp_index)
        env = dict(os.environ, GIT_INDEX_FILE=str(temp_index))

        for segment in _segments_before_first_commit(command):
            if not _GIT_ADD.match(segment):
                continue
            result = subprocess.run(
                segment, shell=True, cwd=project, env=env,
                capture_output=True, text=True, errors="replace",
            )
            if result.returncode:
                raise CommitTreeError(
                    f"could not replay `{segment}` to see what would be committed:\n"
                    f"{_tail(result.stdout + result.stderr)}"
                )
        if commit_includes_tracked_changes(command):
            result = _git(repo_top, "add", "-u", env=env)
            if result.returncode:
                raise CommitTreeError(f"could not apply `commit -a`: {_tail(result.stderr)}")

        tree = temp_root / "tree"
        tree.mkdir()
        result = _git(repo_top, "checkout-index", "-a", f"--prefix={tree}/", env=env)
        if result.returncode:
            raise CommitTreeError(f"could not export the staged files: {_tail(result.stderr)}")

        run_dir = tree / prefix.stdout.strip()
        run_dir.mkdir(parents=True, exist_ok=True)
        for link in links:
            source, target = project / link, run_dir / link
            if source.exists() and not target.exists() and not target.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(source.resolve(), target_is_directory=source.is_dir())
        return temp_root, run_dir
    except BaseException:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def _tail(output: str) -> str:
    lines = output.strip().splitlines()[-_TAIL_LINES:]
    return "\n".join(lines)[-_TAIL_CHARS:]


def evaluate(event, project_dir):
    """Return None to allow the tool call, or a reason string to block it."""
    project = Path(project_dir).resolve()
    if not (project / ".spec").is_dir() or event.get("tool_name") != "Bash":
        return None
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not is_commit(command) or only_test_commits(command):
        return None

    try:
        current = state.read(project)
    except state.StateMissingError:
        return "specforge: commit blocked. .spec/state.json is missing. Run /specforge:spec first."
    except state.StateCorruptError as e:
        return f"specforge: commit blocked. .spec/state.json is corrupt ({e})."
    if not (current["spec_approved"] and current["plan_approved"]):
        return None

    try:
        cfg = config.load(project)
    except config.ConfigError as e:
        return f"specforge: commit blocked. .spec/config.json is invalid ({e})."
    test_command, problem = resolve_command(project, cfg)
    if problem:
        return f"specforge: commit blocked, {problem}"

    temp_root, run_dir, scope_note = None, project, ""
    if cfg["commit_gate_scope"] == "commit" and is_git_repo(project):
        try:
            temp_root, run_dir = build_commit_tree(project, command, cfg["test_env_links"])
        except CommitTreeError as e:
            return f"specforge: commit blocked. {e}"
        scope_note = (
            "\nThe tests ran on exactly what this commit would contain; unstaged and "
            "untracked files were not included. Stage everything the commit needs."
        )
    try:
        result = subprocess.run(
            test_command, shell=True, cwd=run_dir, capture_output=True,
            text=True, errors="replace", timeout=TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"specforge: commit blocked. Tests timed out after {TEST_TIMEOUT}s: {test_command}"
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
    if result.returncode == 0:
        return None
    if result.returncode == 127:
        return f"specforge: commit blocked. Test runner not found (exit 127): {test_command}"
    return (
        f"specforge: commit blocked. Tests failed (exit {result.returncode}): {test_command}\n"
        f"{_tail(result.stdout + result.stderr)}\n"
        "Fix the tests, or commit failing tests with a 'test:' message prefix."
        f"{scope_note}"
    )


def main(argv=None, stdin=None) -> int:
    stdin = stdin or sys.stdin
    try:
        event = json.loads(stdin.read())
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
    except ValueError:
        print("specforge: could not parse hook input; blocking to be safe.", file=sys.stderr)
        return 2

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
    try:
        reason = evaluate(event, project_dir)
    except Exception as e:  # noqa: BLE001 - fail closed on any unexpected error
        print(f"specforge: test gate failed ({e}); blocking to be safe.", file=sys.stderr)
        return 2
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
