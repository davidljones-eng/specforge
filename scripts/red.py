#!/usr/bin/env python3
"""Record RED for the current task: its tests fail as committed at HEAD (M4).

The build runs this in a task's RED step. It works out the current task (the
first unticked one in .spec/plan.md), runs test_filter for that task's own
AC-IDs on a temporary copy of HEAD, and only if those tests fail records the
task and the commit in .spec/state.json. Until then the edit gate and the
commit gate block the task's code. Exit 0 when RED is recorded or not needed,
1 when it is refused, with the reason on stdout.

`specforge red --all` is for the plan phase (M16): it runs every open task's
tests on their own, on the files on disk, and records nothing. It passes only
if each task's tests ran and failed and no test file failed to load, so each
task can go RED -> GREEN on its own in the build.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import config
import state
import test_gate


def run(project):
    """Return (exit_code, message)."""
    project = Path(project).resolve()
    if not (project / ".spec").is_dir():
        return 1, "specforge: no .spec/ folder here. Run /specforge:spec first."
    try:
        current = state.read(project)
    except state.StateMissingError:
        return 1, "specforge: .spec/state.json is missing. Run /specforge:spec first."
    except state.StateCorruptError as e:
        return 1, f"specforge: .spec/state.json is corrupt ({e})."
    if not (current["spec_approved"] and current["plan_approved"]):
        return 1, (
            "specforge: no RED recorded. Approve the spec and the plan first "
            "(/specforge:approve spec, then /specforge:approve plan)."
        )
    changed = state.config_change_reason(project, current)  # M17
    if changed:
        return 1, f"specforge: no RED recorded. {changed.removeprefix('specforge: ')}"
    try:
        cfg = config.load(project)
    except config.ConfigError as e:
        return 1, f"specforge: .spec/config.json is invalid ({e})."
    if not cfg["test_filter"]:
        return 1, (
            'specforge: recording RED needs "test_filter" in .spec/config.json, so that it can run '
            "one task's tests."
        )
    if not cfg["red_first"]:
        return 1, 'specforge: "red_first" is false in .spec/config.json, so no RED is needed or recorded.'

    task = test_gate.current_task(project)
    if task is None:
        return 0, "specforge: .spec/plan.md has no open task, so no RED is needed."
    task_id, acs = task
    if not acs:
        return 0, f"specforge: {task_id} lists no AC-IDs, so it needs no RED."
    recorded = state.red_tasks(current).get(task_id)
    if recorded:
        return 0, f"specforge: RED is already recorded for {task_id} (at {recorded[:7]})."

    head = test_gate._git(project, "rev-parse", "--verify", "-q", "HEAD")
    if head.returncode:
        return 1, (
            f"specforge: no RED recorded. There is no commit yet. Commit the failing tests for "
            f"{', '.join(acs)} with a `test:` prefix first."
        )
    sha = head.stdout.strip()

    temp_root = None
    try:
        try:
            temp_root, run_dir = test_gate.build_head_tree(project, cfg["test_env_links"])
        except test_gate.CommitTreeError as e:
            return 1, f"specforge: no RED recorded. {e}"
        missing = [a for a in acs if not test_gate._has_test_for(run_dir, cfg["test_dirs"], a)]
        if missing:
            return 1, (
                f"specforge: no RED recorded for {task_id}. No test named after "
                f"{', '.join(missing)} is committed at HEAD in {test_gate.folder_list(cfg['test_dirs'])}."
                + (test_gate.missing_tests_note(run_dir, cfg["test_dirs"], missing)
                   or " Write it, commit it with a `test:` prefix, then run `specforge red` again.")
            )
        command = test_gate.filter_command(cfg["test_filter"], acs)
        try:
            result = subprocess.run(
                command, shell=True, cwd=run_dir, capture_output=True,
                text=True, errors="replace", timeout=test_gate.TEST_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return 1, (
                f"specforge: no RED recorded. Tests timed out after "
                f"{test_gate.TEST_TIMEOUT}s: {command}"
            )
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)

    full = test_gate.strip_root(result.stdout + result.stderr, run_dir)
    output = test_gate._tail(full)
    if result.returncode == 127:
        return 1, f"specforge: no RED recorded. Test runner not found (exit 127): {command}\n{output}"
    failures = test_gate.load_failures(full)
    if failures:
        # pytest exits 2 on a collection error, which isn't the tests failing
        return 1, (
            f"specforge: no RED recorded for {task_id}. {test_gate.load_failure_note(failures)}\n{output}"
        )
    if test_gate.tests_ran(full) is False:
        return 1, (
            f"specforge: no RED recorded for {task_id}. The test filter ran no tests: "
            f"{command}\n{output}\nCheck that the runner loads the files with these tests, and "
            "(Jest, Vitest, Mocha) that they aren't inside a describe whose name doesn't start "
            "with the AC-ID."
        )
    if result.returncode == 0:
        return 1, (
            f"specforge: no RED recorded for {task_id}. Its tests for {', '.join(acs)} already "
            f"pass at HEAD:\n{output}\nIf {task_id} still needs code, its tests are too weak: "
            "write a stricter failing test, commit it with a `test:` prefix, and run `specforge red` "
            f"again. If the behavior is already there, tick {task_id} in .spec/plan.md with no "
            "code change and say so in the build summary."
        )
    state.record_red(project, task_id, sha)
    return 0, (
        f"specforge: RED recorded for {task_id} at {sha[:7]}: its tests for "
        f"{', '.join(acs)} fail.\n{output}\nYou can now write {task_id}'s code."
    )


def _task_problems(project, template, tasks, test_dirs):
    """(problems, a test file failed to load, fatal message or None) for running
    `template` for each task's own ACs on the files on disk (M16-AC2)."""
    problems, loads = [], {}  # loads: file -> (error, [task IDs])
    for task_id, acs in tasks:
        missing = [a for a in acs if not test_gate._has_test_for(project, test_dirs, a)]
        if missing:
            problems.append(f"{task_id}: no test named after {', '.join(missing)} in "
                            f"{test_gate.folder_list(test_dirs)}.")
            continue
        command = test_gate.filter_command(template, acs)
        try:
            result = subprocess.run(command, shell=True, cwd=project, capture_output=True,
                                    text=True, errors="replace", timeout=test_gate.TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            problems.append(f"{task_id}: its tests timed out after {test_gate.TEST_TIMEOUT}s: {command}")
            continue
        full = test_gate.strip_root(result.stdout + result.stderr, project)
        if result.returncode == 127:
            return problems, False, f"test runner not found (exit 127): {command}\n{test_gate._tail(full)}"
        failures = test_gate.load_failures(full)
        if failures:
            for f, e in failures:
                loads.setdefault(f, (e, []))[1].append(task_id)
        elif test_gate.tests_ran(full) is False:
            problems.append(f"{task_id}: the test filter ran none of its tests ({', '.join(acs)}): {command}")
        elif result.returncode == 0:
            problems.append(f"{task_id}: its tests for {', '.join(acs)} already pass with no code "
                            "written, so the build couldn't record RED. Make them fail while the "
                            "feature is missing.")
    problems = [f"{f} failed to load, so the tests in it didn't run for {', '.join(ids)}"
                + (f": {e}" if e else ".") for f, (e, ids) in loads.items()] + problems
    return problems, bool(loads), None


def check_all(project):
    """(0, summary) if every open task's tests run on their own, fail, and load;
    else (1, problems). Runs on the working tree and records nothing (M16-AC2).
    Without test_filter, says whether the build will stop and suggests a filter (M19)."""
    project = Path(project).resolve()
    if not (project / ".spec").is_dir():
        return 1, "specforge: no .spec/ folder here. Run /specforge:spec first."
    try:
        changed = state.config_change_reason(project, state.read(project))
    except (state.StateMissingError, state.StateCorruptError):
        changed = None  # no workflow state: nothing was approved
    if changed:
        return 1, changed  # M19-AC4: don't run a command nobody approved (M17)
    try:
        cfg = config.load(project)
    except config.ConfigError as e:
        return 1, f"specforge: .spec/config.json is invalid ({e})."
    if not cfg["test_filter"]:
        return 0, filter_advice(project, cfg)
    tasks = [(tid, acs) for tid, done, acs in test_gate.plan_tasks(project) if not done and acs]
    if not tasks:
        return 0, "specforge: .spec/plan.md has no open task with AC-IDs; nothing to check."

    problems, loads, fatal = _task_problems(project, cfg["test_filter"], tasks, cfg["test_dirs"])
    if fatal:
        return 1, f"specforge: {fatal}"
    names = ", ".join(tid for tid, _ in tasks)
    if problems:
        fix = ("\nLoad the code under test inside each test (await import(...) in the test, or "
               "the import inside the test function for pytest), so a missing module fails only "
               "that test. " if loads else "\n")
        return 1, ("specforge: not every task's tests can go RED -> GREEN on their own in the build:\n"
                   + "\n".join(f"- {p}" for p in problems)
                   + fix + "Then run `specforge red --all` again.")
    return 0, (f"specforge: every task's tests run on their own and fail, and every test file "
               f"loads: {names}. Nothing was recorded.")


# ---------- M19: without test_filter, say the build will stop, and suggest one ----------

_TEST_FILE = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$")
_NODE_TEST = re.compile(r"(\bnode\s+(?:\S+\s+)*?--test)(?=\s|$)")


def build_stop(project):
    """(first open task, [later open tasks with an AC it lacks]) or None when the
    full suite can pass at the first open task's feat: commit (M19-AC1)."""
    open_tasks = [(tid, acs) for tid, done, acs in test_gate.plan_tasks(project) if not done]
    if not open_tasks:
        return None
    first, first_acs = open_tasks[0]
    later = [tid for tid, acs in open_tasks[1:] if set(acs) - set(first_acs)]
    return (first, later) if later else None


def _node_globs(project, cfg):
    """Shell globs for the folders and name patterns of the planned test files."""
    acs = {a for _, done, task_acs in test_gate.plan_tasks(project) if not done for a in task_acs}
    globs = set()
    for ac in acs:
        for rel in test_gate.tests_for(project, cfg["test_dirs"], ac)[0]:
            parent, name = rel.rsplit("/", 1) if "/" in rel else (".", rel)
            match = _TEST_FILE.search(name)
            suffix = match.group(0) if match else Path(name).suffix
            globs.add(f"{shlex.quote(parent)}/*{suffix}")
    return sorted(globs)


def filter_candidates(project, cfg, which=shutil.which):
    """([test_filter candidates, best first], None) for the runner the commit gate
    uses, or ([], why there's none). Nothing is run here (M19-AC2)."""
    command, problem = test_gate.resolve_command(project, cfg, which=which)
    if problem:
        return [], problem
    script = command
    if command.split() in (["npm", "test"], ["npm", "t"], ["npm", "run", "test"]):
        try:
            script = json.loads((Path(project) / "package.json").read_text())["scripts"]["test"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        words = [Path(w).name for w in shlex.split(script)]
    except ValueError:
        words = script.split()
    npx = "" if words[:1] == ["npx"] else "npx "
    if "pytest" in words:
        found = [f"{script} -k {{pytest_k}}"]
    elif _NODE_TEST.search(script):
        found = [_NODE_TEST.sub(r"\1 --test-name-pattern {ac_regex}", script, count=1)]
        globs = _node_globs(project, cfg)
        if globs:
            head = _NODE_TEST.search(script).group(1)
            found.insert(0, f"{head} --test-name-pattern {{ac_regex}} {' '.join(globs)}")
    elif "jest" in words:
        found = [f"{npx}{script} -t {{ac_regex}}"]
    elif "vitest" in words:
        if "run" not in words:
            script = re.sub(r"\bvitest\b", "vitest run", script, count=1)
        found = [f"{npx}{script} -t {{ac_regex}}"]
    elif "mocha" in words:
        found = [f"{npx}{script} --grep {{ac_regex}}"]
    elif words[:2] == ["flutter", "test"]:
        found = [f"{script} --name {{ac_regex}}"]
    else:
        return [], f"the commit gate runs `{command}`, and there's no suggestion for that runner."
    return list(dict.fromkeys(found)), None


def filter_advice(project, cfg):
    """What red --all says when test_filter isn't set (M19-AC1, AC2)."""
    stop = build_stop(project)
    if stop is None:
        return ('specforge: "test_filter" isn\'t set in .spec/config.json, so the commit gate runs '
                "the full suite on every commit. The first open task covers every planned AC, "
                "so that's enough; nothing to check.")
    first, later = stop
    warning = (
        f'specforge: warning: "test_filter" isn\'t set in .spec/config.json, so the build will '
        f"stop at {first}. The commit gate runs the full suite, which keeps failing until "
        f"{', '.join(later)} {'is' if len(later) == 1 else 'are'} done too, so {first}'s `feat:` "
        "commit is blocked."
    )
    after = ("Add it before /specforge:approve plan: once the plan is approved, a change to "
             ".spec/config.json means approving the plan again.")
    candidates, problem = filter_candidates(project, cfg)
    tasks = [(tid, acs) for tid, done, acs in test_gate.plan_tasks(project) if not done and acs]
    tried = []
    for candidate in candidates:
        problems, _, fatal = _task_problems(project, candidate, tasks, cfg["test_dirs"])
        if not problems and not fatal:
            return (f"{warning}\nSuggested, and checked: with it, every task's tests ran on their "
                    "own and failed, and every test file loaded:\n"
                    f'  "test_filter": {json.dumps(candidate)}\n{after}')
        tried.append(f'- "{candidate}": {(fatal or problems[0]).splitlines()[0]}')
    why = problem or "none passed the check. Tried:\n" + "\n".join(tried)
    return (f"{warning}\nNo filter to suggest: {why}\nSet \"test_filter\" yourself (see the "
            "README's Configuration section), then run `specforge red --all` to check it. "
            f"{after}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="specforge red", description=__doc__.splitlines()[0])
    parser.add_argument("--project-dir")
    parser.add_argument("--all", action="store_true",
                        help="check every open task's tests at plan time; record nothing")
    args = parser.parse_args(argv)
    project = args.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        code, message = check_all(project) if args.all else run(project)
    except Exception as e:  # noqa: BLE001 - report, never record on an unexpected error
        code, message = 1, f"specforge: red failed ({e}); no RED recorded."
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
