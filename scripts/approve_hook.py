#!/usr/bin/env python3
"""UserPromptSubmit hook that records approvals (M5).

Claude Code runs this hook only for prompts a person submits, and passes the
prompt as typed, before a slash command is expanded. When the prompt runs
/specforge:approve, this script checks the target and records the approval, or
blocks the prompt with the reason. Every other prompt passes through untouched.
The model has no way to record an approval: state.py has no approve command
and gate_check.py blocks the shell routes to it.
"""
import json
import os
import re
import sys
from pathlib import Path

import state

COMMAND = "/specforge:approve"
USAGE = "Usage: /specforge:approve spec|plan"
_AC_ID = re.compile(r"\bAC-\d+\b")


def parse(prompt):
    """The command's arguments if `prompt` runs /specforge:approve, else None.

    Claude Code runs a slash command when the prompt starts with it, with no
    leading space, and passes the rest as the arguments.
    """
    if not isinstance(prompt, str) or not prompt.startswith(COMMAND):
        return None
    rest = prompt[len(COMMAND):]
    if rest and not rest[0].isspace():
        return None  # a different command, such as /specforge:approver
    return rest.split()


def _block(reason):
    return "block", f"specforge: approval not recorded. {reason}"


def evaluate(prompt, project_dir):
    """None for a prompt that is not the command, else ("ok"|"block", message)."""
    args = parse(prompt)
    if args is None:
        return None
    if args not in (["spec"], ["plan"]):
        return _block(USAGE)
    target = args[0]

    project = Path(project_dir)
    spec_dir = project / ".spec"
    if not spec_dir.is_dir():
        return _block("This project has no .spec/ folder. Run /specforge:spec first.")
    try:
        current = state.read(project)
    except state.StateMissingError:
        return _block(".spec/state.json is missing. Run /specforge:spec first.")
    except state.StateCorruptError as e:
        return _block(f".spec/state.json is corrupt ({e}). Fix or delete it, then run /specforge:spec.")

    if target == "spec":
        spec = spec_dir / "spec.md"
        if not spec.is_file():
            return _block(".spec/spec.md does not exist. Run /specforge:spec first.")
        ac_ids = set(_AC_ID.findall(spec.read_text(errors="replace")))
        if not ac_ids:
            return _block(
                ".spec/spec.md has no acceptance criteria with AC-IDs (AC-1, AC-2, …). "
                "Run /specforge:spec to add them."
            )
        data = state.approve(project, "spec")
        return "ok", (
            f"specforge: the approval hook recorded the user's approval of the spec "
            f"(spec version {data['spec_version']}, {len(ac_ids)} acceptance criteria)."
        )

    if not current["spec_approved"]:
        return _block("The spec is not approved yet. Review .spec/spec.md, then run /specforge:approve spec.")
    if not (spec_dir / "plan.md").is_file():
        return _block(".spec/plan.md does not exist. Run /specforge:plan first.")
    state.approve(project, "plan")
    return "ok", "specforge: the approval hook recorded the user's approval of the plan."


def main(stdin=None) -> int:
    stdin = stdin or sys.stdin
    try:
        event = json.loads(stdin.read())
    except ValueError:
        return 0  # nothing recorded; never block the user's prompt on bad input
    if not isinstance(event, dict) or parse(event.get("prompt")) is None:
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
    try:
        decision, message = evaluate(event["prompt"], project_dir)
    except Exception as e:  # noqa: BLE001 - an approval that can't be checked isn't recorded
        decision, message = _block(f"The approval check failed ({e}).")

    if decision == "block":
        print(json.dumps({"decision": "block", "reason": message}))
    else:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": message,
            }
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
