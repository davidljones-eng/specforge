#!/usr/bin/env python3
"""UserPromptSubmit hook that records approvals (M5) and withdraws them (M6).

Claude Code runs this hook only for prompts a person submits, and passes the
prompt as typed, before a slash command is expanded. When the prompt runs
/specforge:approve or /specforge:unapprove, this script checks the target and
records the change, or blocks the prompt with the reason. Every other prompt
passes through untouched. The model has no way to change an approval: state.py
has no approve or unapprove command, and gate_check.py blocks the routes to them.
"""
import json
import os
import re
import sys
from pathlib import Path

import state

APPROVE = "/specforge:approve"
UNAPPROVE = "/specforge:unapprove"
COMMANDS = (APPROVE, UNAPPROVE)
# Prompts that show the session the hidden checks (M11), so it may not build.
AWARE_COMMANDS = ("/specforge:spec", "/specforge:plan", "/specforge:review")
_AC_ID = re.compile(r"\bAC-\d+\b")


def parse(prompt):
    """(command, arguments) if `prompt` runs one of COMMANDS, else None.

    Claude Code runs a slash command when the prompt starts with it, with no
    leading space, and passes the rest as the arguments.
    """
    if not isinstance(prompt, str):
        return None
    for command in COMMANDS:
        if prompt.startswith(command):
            rest = prompt[len(command):]
            if rest and not rest[0].isspace():
                continue  # a different command, such as /specforge:approver
            return command, rest.split()
    return None


def runs_aware_command(prompt):
    """The command if `prompt` runs one of AWARE_COMMANDS, else None."""
    if not isinstance(prompt, str):
        return None
    for command in AWARE_COMMANDS:
        if prompt.startswith(command):
            rest = prompt[len(command):]
            if not rest or rest[0].isspace():
                return command
    return None


def record_session(project_dir, command, session_id):
    """Mark the session as having seen the hidden checks. The first
    /specforge:spec in a project creates the state for it."""
    project = Path(project_dir)
    try:
        state.read(project)
    except state.StateMissingError:
        if command != "/specforge:spec":
            return  # the command will refuse: there is no workflow yet
        state.create(project)
    state.record_session(project, session_id)


def _block(reason):
    return "block", f"specforge: approval not recorded. {reason}"


def evaluate(prompt, project_dir):
    """None for a prompt that is not one of COMMANDS, else ("ok"|"block", message)."""
    parsed = parse(prompt)
    if parsed is None:
        return None
    command, args = parsed
    if args not in (["spec"], ["plan"]):
        return _block(f"Usage: {command} spec|plan")
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

    if command == UNAPPROVE:
        return _unapprove(project, current, target)
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


def _unapprove(project, current, target):
    if not current[f"{target}_approved"]:
        return "block", f"specforge: the {target} is not approved, so there is nothing to withdraw."
    data = state.unapprove(project, target)
    withdrawn = "the spec and the plan" if target == "spec" else "the plan"
    return "ok", (
        f"specforge: the approval hook withdrew the user's approval of {withdrawn}. "
        f"The phase is back to {data['phase']}. Approve again with /specforge:approve "
        f"{'spec' if target == 'spec' else 'plan'}."
    )


def main(stdin=None) -> int:
    stdin = stdin or sys.stdin
    try:
        event = json.loads(stdin.read())
    except ValueError:
        return 0  # nothing recorded; never block the user's prompt on bad input
    if not isinstance(event, dict):
        return 0
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()

    aware = runs_aware_command(event.get("prompt"))
    if aware:
        session_id = event.get("session_id")
        if isinstance(session_id, str) and session_id:
            try:
                record_session(project_dir, aware, session_id)
            except Exception as e:  # noqa: BLE001 - unrecorded, the session could build after seeing hidden checks
                print(json.dumps({"decision": "block", "reason": (
                    f"specforge: couldn't record that this session ran {aware} ({e}). "
                    "Fix .spec/state.json, then run the command again.")}))
        return 0

    if parse(event.get("prompt")) is None:
        return 0
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
