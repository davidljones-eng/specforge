#!/usr/bin/env python3
"""PreToolUse hook script: block `git commit` while the project's tests fail.

Commits whose message starts with `test:` are let through so that failing tests
(the RED step of TDD) can be committed. The gate only applies once both the spec
and the plan are approved. Exit 0 allows, exit 2 blocks with a reason on stderr.

By default (commit_gate_scope "commit") the suite runs on exactly what would be
committed: the staged files, plus any `git add ...` in the same command replayed
into a temporary index, plus tracked changes for `commit -a`. For `git commit
<paths>` it is HEAD plus the copies on disk of the tracked files named (`-i`
keeps the other staged changes too), as git does. That copy lives in
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


_TASK_LINE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*T\d+\b(?P<rest>.*)$")
_AC_ID = re.compile(r"\bAC-\d+\b")
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
_MAX_SCAN_BYTES = 2_000_000
_DOC_SUFFIXES = {".md", ".markdown", ".rst", ".txt"}


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


_SHELL_OPERATOR_CHARS = "();<>|&\n"
# Long options whose value is the next token when it isn't given with "=".
_COMMIT_LONG_OPTS_WITH_VALUE = {
    "--message", "--file", "--author", "--date", "--template", "--reuse-message",
    "--reedit-message", "--fixup", "--squash", "--cleanup", "--trailer",
    "--pathspec-from-file",
}
_COMMIT_OPTS_OPTIONAL_VALUE = set("uS")  # -uno, -S<keyid>: the value is attached


def _commit_tokens(rest: str):
    """The arguments of a `git commit`, up to the first shell operator."""
    lexer = shlex.shlex(rest, posix=True, punctuation_chars=_SHELL_OPERATOR_CHARS)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    tokens = []
    try:
        for tok in lexer:
            if tok and set(tok) <= set(_SHELL_OPERATOR_CHARS):
                if tok[0] in "<>" and tokens and tokens[-1].isdigit():
                    tokens.pop()  # the "2" of "2>&1"
                break
            tokens.append(tok)
    except ValueError:  # unbalanced quotes: fall back to the first line
        tokens = _SEGMENT_SPLIT.split(rest)[0].split()
    return tokens


def commit_args(command: str) -> dict:
    """What the first `git commit` in the command asks for.

    "paths" are the pathspecs it names, "mode" is "include" for -i / --include
    and "only" otherwise, "all" is -a / --all, and "pathspec_file" is
    --pathspec-from-file.
    """
    args = {"all": False, "mode": "only", "paths": [], "pathspec_file": False}
    match = _COMMIT.search(command)
    if not match:
        return args
    skip = after_dashes = False
    for tok in _commit_tokens(command[match.end():]):
        if skip:
            skip = False
        elif after_dashes or not tok.startswith("-") or tok == "-":
            args["paths"].append(tok)
        elif tok == "--":
            after_dashes = True
        elif tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name == "--all":
                args["all"] = True
            elif name == "--include":
                args["mode"] = "include"
            elif name == "--only":
                args["mode"] = "only"
            elif name == "--pathspec-from-file":
                args["pathspec_file"] = True
            skip = "=" not in tok and name in _COMMIT_LONG_OPTS_WITH_VALUE
        else:
            letters = tok[1:]
            for i, letter in enumerate(letters):
                if letter == "a":
                    args["all"] = True
                elif letter == "i":
                    args["mode"] = "include"
                elif letter == "o":
                    args["mode"] = "only"
                if letter in _COMMIT_OPTS_OPTIONAL_VALUE:
                    break
                if letter in _COMMIT_OPTS_WITH_VALUE:
                    skip = i == len(letters) - 1  # value is the next token
                    break
    return args


def commit_includes_tracked_changes(command: str) -> bool:
    """True if the first `git commit` in the command uses -a / --all."""
    return commit_args(command)["all"]


def _stage_named_paths(project: Path, env, args, has_head):
    """Make the temporary index hold what `git commit <paths>` would commit.

    Like git: the tracked files the paths match (with --only, also files
    deleted since HEAD) are taken from disk. --only starts from HEAD, so other
    staged changes are left out; --include keeps them. Untracked files are never
    added.
    """
    listing = ["ls-files", "-z"]
    if args["mode"] == "only":
        listing.append("--error-unmatch")
        if has_head:
            listing.append("--with-tree=HEAD")
    result = _git(project, *listing, "--", *args["paths"], env=env)
    if result.returncode:
        raise CommitTreeError(
            f"git would refuse this commit: {_tail(result.stderr).strip()}"
        )
    files = list(dict.fromkeys(p for p in result.stdout.split("\0") if p))
    if args["mode"] == "only":
        result = _git(project, "read-tree", "HEAD" if has_head else "--empty", env=env)
        if result.returncode:
            raise CommitTreeError(f"could not start from HEAD: {_tail(result.stderr)}")
    if not files:
        return
    result = subprocess.run(
        ["git", "update-index", "--add", "--remove", "-z", "--stdin"],
        cwd=project, env=env, input="\0".join(files) + "\0",
        capture_output=True, text=True, errors="replace",
    )
    if result.returncode:
        raise CommitTreeError(
            f"could not take the named files from disk: {_tail(result.stderr)}"
        )


def build_commit_tree(project: Path, command: str, links):
    """Export what `command` would commit into a temporary directory.

    Returns (temp_root, run_dir, changed_paths). The caller must remove temp_root.
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
        has_head = _git(repo_top, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
        args = commit_args(command)
        if args["pathspec_file"]:
            raise CommitTreeError(
                "`git commit --pathspec-from-file` is not supported by the gate. Stage the "
                "files with `git add` and commit without naming paths."
            )
        if args["paths"]:
            if args["all"]:
                raise CommitTreeError("git refuses `commit -a` together with paths.")
            _stage_named_paths(project, env, args, has_head)
        elif args["all"]:
            result = _git(repo_top, "add", "-u", env=env)
            if result.returncode:
                raise CommitTreeError(f"could not apply `commit -a`: {_tail(result.stderr)}")

        tree = temp_root / "tree"
        tree.mkdir()
        result = _git(repo_top, "checkout-index", "-a", f"--prefix={tree}/", env=env)
        if result.returncode:
            raise CommitTreeError(f"could not export the staged files: {_tail(result.stderr)}")

        listing = (
            _git(repo_top, "diff-index", "--cached", "--name-only", "HEAD", env=env)
            if has_head else _git(repo_top, "ls-files", env=env)
        )
        if listing.returncode:
            raise CommitTreeError(f"could not list the changed files: {_tail(listing.stderr)}")
        changed = [line for line in listing.stdout.splitlines() if line.strip()]

        run_dir = tree / prefix.stdout.strip()
        run_dir.mkdir(parents=True, exist_ok=True)
        for link in links:
            source, target = project / link, run_dir / link
            if source.exists() and not target.exists() and not target.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(source.resolve(), target_is_directory=source.is_dir())
        return temp_root, run_dir, changed
    except BaseException:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def acs_in_scope(project, include_current=True):
    """AC-IDs of the done tasks, plus the first open task's when include_current.

    None means "run the whole suite": there is no plan, no task list, or every
    task is done. An empty list means there is nothing to test yet.
    """
    plan = Path(project) / ".spec" / "plan.md"
    if not plan.is_file():
        return None
    tasks = []
    for line in plan.read_text(errors="replace").splitlines():
        match = _TASK_LINE.match(line)
        if match:
            tasks.append((match.group(1) != " ", _AC_ID.findall(match.group("rest"))))
    if not tasks or all(done for done, _ in tasks):
        return None
    first_open = next(i for i, (done, _) in enumerate(tasks) if not done)
    ids = []
    for i, (done, acs) in enumerate(tasks):
        if done or (include_current and i == first_open):
            ids.extend(a for a in acs if a not in ids)
    return ids


def ac_regex(ids):
    """A regex matching test names that start with one of the IDs.

    A test belongs to the AC its name starts with: "AC-9: sends as in AC-2" is
    an AC-9 test, so it must not run in AC-2's scope.
    """
    return r"^(?:" + "|".join(ids) + r")\b"


def pytest_k(ids):
    """A pytest -k expression for test functions named test_ac<n>_...

    The test_ prefix keeps "test_ac9_same_as_ac2_" out of AC-2's scope.
    """
    return " or ".join(f"test_ac{i.split('-')[1]}_" for i in ids)


def _test_name_patterns(ac_id):
    """Where an AC's own test name starts: a string literal that opens with the
    AC-ID ("AC-2: ...", 'AC-2 ...', `AC-2 ...`), or a function test_ac2_...
    A mention later in a name or in a comment does not count."""
    number = ac_id.split("-")[1]
    return (
        re.compile(rf"""["'`]AC-{number}\b"""),
        re.compile(rf"\btest_ac{number}_", re.IGNORECASE),
    )


def _has_test_for(run_dir: Path, test_dirs, ac_id) -> bool:
    patterns = _test_name_patterns(ac_id)
    for root, dirs, files in os.walk(run_dir, followlinks=False):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        rel_parts = Path(root).relative_to(run_dir).parts
        if not any(part in test_dirs for part in rel_parts):
            continue
        for name in files:
            path = Path(root) / name
            if path.suffix.lower() in _DOC_SUFFIXES:
                continue  # a guide that mentions an AC-ID is not a test for it
            try:
                if path.stat().st_size > _MAX_SCAN_BYTES:
                    continue
                text = path.read_text(errors="ignore")
                if any(p.search(text) for p in patterns):
                    return True
            except OSError:
                continue
    return False


def touches_code(paths, cfg) -> bool:
    """True if any non-doc path is inside a code folder (and not a test folder)."""
    for path in paths:
        if Path(path).suffix.lower() in _DOC_SUFFIXES:
            continue  # folder CLAUDE.md guides and other docs are not code
        folders = Path(path).parts[:-1]
        if any(f in cfg["code_dirs"] for f in folders) and not any(
            f in cfg["test_dirs"] for f in folders
        ):
            return True
    return False


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

    temp_root, run_dir, notes, changed = None, project, [], None
    try:
        if cfg["commit_gate_scope"] == "commit" and is_git_repo(project):
            try:
                temp_root, run_dir, changed = build_commit_tree(
                    project, command, cfg["test_env_links"]
                )
            except CommitTreeError as e:
                return f"specforge: commit blocked. {e}"
            notes.append(
                "The tests ran on exactly what this commit would contain; unstaged and "
                "untracked files were not included. Stage everything the commit needs."
            )

        # Code for the current task needs the current task's tests. A commit that
        # changes no code (or whose changes are unknown: worktree scope) is held to
        # the done tasks only, or to everything when the changes are unknown.
        code_change = changed is None or touches_code(changed, cfg)
        scope = acs_in_scope(project, include_current=code_change) if cfg["test_filter"] else None
        if scope == []:
            return None  # no code change and no task done yet: nothing to test
        if scope:
            missing = [a for a in scope if not _has_test_for(run_dir, cfg["test_dirs"], a)]
            if missing:
                return (
                    f"specforge: commit blocked. No tests found for {', '.join(missing)} in "
                    f"{', '.join(cfg['test_dirs'])}. Every AC of a done or current task needs "
                    "a test named after its AC-ID before code for it can be committed."
                )
            test_command = (
                cfg["test_filter"]
                .replace("{ac_regex}", shlex.quote(ac_regex(scope)))
                .replace("{pytest_k}", shlex.quote(pytest_k(scope)))
            )
            notes.append(
                f"Only the tests for done tasks and the current task were run: {', '.join(scope)}."
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
    note_text = "".join(f"\n{n}" for n in notes)
    return (
        f"specforge: commit blocked. Tests failed (exit {result.returncode}): {test_command}\n"
        f"{_tail(result.stdout + result.stderr)}\n"
        "Fix the tests, or commit failing tests with a 'test:' message prefix."
        f"{note_text}"
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
