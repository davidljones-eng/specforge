#!/usr/bin/env python3
"""Workflow state for specforge, stored in <project>/.spec/state.json."""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

PHASES = ("spec", "plan", "build", "review")
BUILD_MODES = ("strict", "checkpoint", "auto")

DEFAULT_STATE = {
    "phase": "spec",
    "spec_approved": False,
    "plan_approved": False,
    "spec_version": 1,
    "build_mode": "checkpoint",
}

# Optional keys, absent until first used, so a state.json from before them
# still reads. "red" maps a task ID to the commit where red.py saw its tests
# fail (M4); only red.py writes it.
OPTIONAL_KEYS = ("red",)
_TASK_ID = re.compile(r"^T\d+$")


class StateMissingError(Exception):
    """No .spec/state.json exists in the project."""


class StateCorruptError(Exception):
    """state.json exists but is not valid."""


def state_path(project_dir) -> Path:
    return Path(project_dir) / ".spec" / "state.json"


def exists(project_dir) -> bool:
    return state_path(project_dir).is_file()


def _validate(data):
    """Raise ValueError if `data` is not a valid state dict."""
    if not isinstance(data, dict):
        raise ValueError("state must be a JSON object")
    keys = set(data)
    if not set(DEFAULT_STATE) <= keys or keys - set(DEFAULT_STATE) - set(OPTIONAL_KEYS):
        raise ValueError(
            f"state keys must be exactly {sorted(DEFAULT_STATE)}, plus optionally "
            f"{list(OPTIONAL_KEYS)}"
        )
    if data["phase"] not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    if data["build_mode"] not in BUILD_MODES:
        raise ValueError(f"build_mode must be one of {BUILD_MODES}")
    for key in ("spec_approved", "plan_approved"):
        if not isinstance(data[key], bool):
            raise ValueError(f"{key} must be true or false")
    version = data["spec_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("spec_version must be an integer >= 1")
    if data["plan_approved"] and not data["spec_approved"]:
        raise ValueError("plan cannot be approved before the spec")
    if "red" in data:
        red = data["red"]
        if not isinstance(red, dict) or not all(
            isinstance(k, str) and _TASK_ID.match(k) and isinstance(v, str) and v
            for k, v in red.items()
        ):
            raise ValueError("red must map task IDs like T4 to commit IDs")


def _write(project_dir, data):
    path = state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def create(project_dir, build_mode="checkpoint") -> dict:
    data = {**DEFAULT_STATE, "build_mode": build_mode}
    _validate(data)
    if exists(project_dir):
        raise FileExistsError(f"{state_path(project_dir)} already exists")
    _write(project_dir, data)
    return data


def read(project_dir) -> dict:
    path = state_path(project_dir)
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise StateMissingError(str(path)) from None
    try:
        data = json.loads(text)
        _validate(data)
    except ValueError as e:  # json.JSONDecodeError is a ValueError
        raise StateCorruptError(str(e)) from None
    return data


def update(project_dir, **changes) -> dict:
    data = read(project_dir)
    unknown = set(changes) - set(DEFAULT_STATE) - set(OPTIONAL_KEYS)
    if unknown:
        raise ValueError(f"unknown state keys: {sorted(unknown)}")
    data.update(changes)
    _validate(data)
    _write(project_dir, data)
    return data


def approve(project_dir, target) -> dict:
    """Record an approval. Only approve_hook.py calls this outside tests (M5)."""
    if target == "spec":
        return update(project_dir, spec_approved=True)
    if target == "plan":
        if not read(project_dir)["spec_approved"]:
            raise ValueError("the spec must be approved before the plan")
        # A newly approved plan may number its tasks differently, so earlier RED
        # records no longer apply.
        data = read(project_dir)
        data.pop("red", None)
        data["plan_approved"] = True
        _validate(data)
        _write(project_dir, data)
        return data
    raise ValueError("approve target must be 'spec' or 'plan'")


def red_tasks(data) -> dict:
    """Task ID -> commit for the tasks whose tests red.py saw failing."""
    return dict(data.get("red", {}))


def record_red(project_dir, task_id, commit) -> dict:
    """Record that `task_id`'s tests failed at `commit`. Used only by red.py."""
    return update(project_dir, red={**red_tasks(read(project_dir)), task_id: commit})


def bump_spec_version(project_dir) -> dict:
    version = read(project_dir)["spec_version"]
    return update(
        project_dir,
        spec_version=version + 1,
        spec_approved=False,
        plan_approved=False,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="state.py")
    parser.add_argument("--project-dir", default=".")
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init")
    init.add_argument("--mode", default="checkpoint")
    sub.add_parser("get")
    sub.add_parser("bump")
    sub.add_parser("set-phase").add_argument("phase")
    sub.add_parser("set-mode").add_argument("mode")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "init":
            result = create(args.project_dir, build_mode=args.mode)
        elif args.cmd == "get":
            result = read(args.project_dir)
        elif args.cmd == "bump":
            result = bump_spec_version(args.project_dir)
        elif args.cmd == "set-phase":
            result = update(args.project_dir, phase=args.phase)
        else:
            result = update(args.project_dir, build_mode=args.mode)
    except StateMissingError:
        print("No .spec/state.json found. Run /specforge:spec first.", file=sys.stderr)
        return 1
    except StateCorruptError as e:
        print(f".spec/state.json is corrupt: {e}", file=sys.stderr)
        return 1
    except FileExistsError:
        print("State already exists; refusing to overwrite it.", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
