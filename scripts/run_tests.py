"""Run the tests the way the commit gate does (M18).

    specforge test              the full suite: test_command, or the runner the
                                gate detects (pytest -q, npm test, flutter test)
    specforge test AC-1 AC-2    with test_filter set, only those ACs' tests

The phase commands run the tests only this way, so a headless run needs no
rule for the runner: Bash(specforge:*) covers it, and the model runs exactly
what the gate will. This gives no access the gate doesn't already use: the
commit hook runs test_command on every commit, and `specforge red` runs
test_filter. It runs on the working folder, not on a copy of the commit.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

import config
import test_gate

_AC_ID = re.compile(r"^AC-\d+$")


def main(argv=None, run=subprocess.run, which=shutil.which) -> int:
    parser = argparse.ArgumentParser(prog="specforge test")
    parser.add_argument("--project-dir")
    parser.add_argument("ids", nargs="*", metavar="AC-ID")
    args = parser.parse_args(argv)
    bad = [i for i in args.ids if not _AC_ID.match(i)]
    if bad:
        print(f"specforge: {', '.join(bad)} isn't an AC-ID. usage: specforge test [AC-ID ...]")
        return 2
    project = args.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        cfg = config.load(project)
    except config.ConfigError as e:
        print(f"specforge: .spec/config.json is invalid ({e}).")
        return 1
    command, problem = test_gate.resolve_command(project, cfg, which=which)
    if problem:
        print(f"specforge: {problem} Stop and ask the user to set it; don't run the tests another way.")
        return 1
    note = None
    if args.ids:
        if cfg["test_filter"]:
            command = test_gate.filter_command(cfg["test_filter"], args.ids)
        else:
            note = "test_filter isn't set, so this runs the whole suite, not only these ACs' tests."
    print(f"specforge test: {command}", flush=True)
    if note:
        print(f"specforge: {note}", flush=True)
    try:
        result = run(command, shell=True, cwd=project, timeout=test_gate.TEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"specforge: the tests timed out after {test_gate.TEST_TIMEOUT}s: {command}")
        return 1
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
