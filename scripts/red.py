#!/usr/bin/env python3
"""Record RED for the current task: its tests fail as committed at HEAD (M4).

The build runs this in a task's RED step. It works out the current task (the
first unticked one in .spec/plan.md), runs test_filter for that task's own
AC-IDs on a temporary copy of HEAD, and only if those tests fail records the
task and the commit in .spec/state.json. Until then the edit gate and the
commit gate block the task's code. Exit 0 when RED is recorded or not needed,
1 when it is refused, with the reason on stdout.
"""
import argparse
import os
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
    try:
        cfg = config.load(project)
    except config.ConfigError as e:
        return 1, f"specforge: .spec/config.json is invalid ({e})."
    if not cfg["test_filter"]:
        return 1, (
            'specforge: red.py needs "test_filter" in .spec/config.json, so that it can run '
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
                f"{', '.join(missing)} is committed at HEAD. Write it, commit it with a "
                "`test:` prefix, then run red.py again."
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

    full = result.stdout + result.stderr
    output = test_gate._tail(full)
    if result.returncode == 127:
        return 1, f"specforge: no RED recorded. Test runner not found (exit 127): {command}\n{output}"
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
            "write a stricter failing test, commit it with a `test:` prefix, and run red.py "
            f"again. If the behavior is already there, tick {task_id} in .spec/plan.md with no "
            "code change and say so in the build summary."
        )
    state.record_red(project, task_id, sha)
    return 0, (
        f"specforge: RED recorded for {task_id} at {sha[:7]}: its tests for "
        f"{', '.join(acs)} fail.\n{output}\nYou can now write {task_id}'s code."
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="red.py", description=__doc__.splitlines()[0])
    parser.add_argument("--project-dir")
    args = parser.parse_args(argv)
    project = args.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        code, message = run(project)
    except Exception as e:  # noqa: BLE001 - report, never record on an unexpected error
        code, message = 1, f"specforge: red.py failed ({e}); no RED recorded."
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
