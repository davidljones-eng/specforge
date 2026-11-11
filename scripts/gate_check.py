#!/usr/bin/env python3
"""Hook script that enforces specforge's approval gates.

PreToolUse (default): exit 0 to allow, exit 2 with a reason on stderr to block.
Bash commands and Skill calls that would approve or unapprove are blocked: only
approve_hook.py changes an approval, from a prompt the user submits (M5, M6).
With test_filter set, code for the current task also waits until red.py has
seen that task's tests fail (M4, red_first).
PostToolUse (--post): after spec.md changes post-approval, bump the spec version
and reset approvals.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import config
import holdout
import state
import test_gate

EDIT_TOOLS = ("Edit", "Write", "MultiEdit")
READ_TOOLS = ("Read", "Grep", "Glob")

NEED_SPEC = "need_spec"  # tests, and CLAUDE.md notes inside code folders
NEED_BOTH = "need_both"  # source code
OPEN = "open"

# Rough test for a shell command that could modify a file. Best effort only.
_WRITE_HINT = re.compile(
    r"(>|\b(tee|mv|cp|rm|truncate|dd|ed|ln|install)\b"
    r"|\bsed\b[^|;&]*\s-\w*i"
    r"|\b(python3?|perl|ruby|node)\b)"
)
_HARMLESS_REDIRECT = re.compile(r"\d*>&\d*-?|\d*>\s*/dev/null")

# M5, M6: approvals are recorded and withdrawn only by approve_hook.py, from a
# prompt the user submits. Quotes and backslashes are dropped first, so
# st"ate".py still matches.
_STATE_MODULE = re.compile(r"state\.py|\bimport\s+state\b|\bfrom\s+state\s+import\b")
_APPROVE = re.compile(r"\b(?:un)?approve\b")
_APPROVE_COMMAND = re.compile(r"specforge:(?:un)?approve\b")
_APPROVAL_SKILLS = ("specforge:approve", "specforge:unapprove")
_ONLY_THE_USER = (
    "specforge: only the user can approve or withdraw an approval. It's recorded when the "
    "user submits /specforge:approve spec|plan or /specforge:unapprove spec|plan"
)


def classify(rel_parts, cfg) -> str:
    dirs, name = rel_parts[:-1], rel_parts[-1]
    if any(d in cfg["test_dirs"] for d in dirs):
        return NEED_SPEC
    if any(d in cfg["code_dirs"] for d in dirs):
        return NEED_SPEC if name == "CLAUDE.md" else NEED_BOTH
    return OPEN


def _target_parts(event, project: Path):
    """Path of the edited file relative to the project, or None if outside it."""
    tool_input = event.get("tool_input")
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path:
        raise ValueError("no file_path in tool input")
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(event.get("cwd") or project) / path
    try:
        return path.resolve().relative_to(project).parts
    except ValueError:
        return None


def _evaluate_bash(event, project: Path):
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return None
    bare = re.sub(r"[\"'\\]", "", command)
    if _APPROVE_COMMAND.search(bare) or (_STATE_MODULE.search(bare) and _APPROVE.search(bare)):
        return f"{_ONLY_THE_USER}; ask them to run it."
    if "state.json" not in command:
        return None
    if _WRITE_HINT.search(_HARMLESS_REDIRECT.sub("", command)):
        return (
            "specforge: .spec/state.json cannot be modified through Bash. "
            "Use /specforge:approve spec|plan to record an approval, or "
            "/specforge:unapprove spec|plan to withdraw one."
        )
    return None


def _evaluate_skill(event):
    """Block the model loading /specforge:approve or /specforge:unapprove, which
    disable-model-invocation does not stop when the user asks for it. Only a
    prompt the user submits changes an approval, so loading one would only confuse."""
    tool_input = event.get("tool_input")
    name = tool_input.get("skill") if isinstance(tool_input, dict) else None
    if isinstance(name, str) and name.lstrip("/") in _APPROVAL_SKILLS:
        return (
            f"{_ONLY_THE_USER}. Ask the user to type /{name.lstrip('/')} spec|plan themselves; "
            "it does nothing when you run it."
        )
    return None


# M11, best effort: shell commands that search the whole project.
_SEARCH = re.compile(
    r"\b(?:e|f)?grep\b[^|;&]*\s-[A-Za-z]*[rR]|\bgrep\b[^|;&]*--recursive"
    r"|\b(?:rg|ag|ack)\b|\bgit\s+grep\b|\bfind\b"
)
_SPEC_FOLDER = re.compile(r"\.spec/?(?:\*|\s|$|[;|&])")


def _search_dirs(project):
    try:
        cfg = config.load(project)
    except config.ConfigError:
        cfg = config.DEFAULT_CONFIG
    return list(cfg["code_dirs"]) + list(cfg["test_dirs"])


def _hidden_applies(event, project):
    """True if this call comes from a session that may build while hidden ACs exist."""
    return holdout.exists(project) and not holdout.session_aware(project, event.get("session_id"))


def _evaluate_read(event, project):
    """M11: a session that may build can't read or search the hidden material."""
    if not _hidden_applies(event, project):
        return None
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    cwd = event.get("cwd") or str(project)
    tool = event.get("tool_name")
    if tool == "Read":
        file_path = tool_input.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return "specforge: blocked a read because the target file could not be determined."
        path = Path(file_path) if Path(file_path).is_absolute() else Path(cwd) / file_path
        if holdout.is_hidden(project, path):
            return (
                f"specforge: blocked reading {holdout.relative(project, path)}. It holds hidden "
                "checks that a session which may build mustn't see. Work from .spec/spec.md "
                "and .spec/plan.md."
            )
        return None
    hits = (holdout.grep_reaches if tool == "Grep" else holdout.glob_reaches)(project, tool_input, cwd)
    if not hits:
        return None
    shown = ", ".join(holdout.relative(project, h) for h in hits[:3])
    return (
        f"specforge: blocked a search that would reach hidden checks ({shown}). "
        f"Search inside {', '.join(_search_dirs(project))} instead, for example with path: "
        f"{_search_dirs(project)[0]}."
    )


def _evaluate_hidden_bash(event, project, command):
    """M11, best effort: keep a session that may build out of the hidden material."""
    if not _hidden_applies(event, project):
        return None
    names = sorted(n for n in holdout.hidden_names(project) if n in command)
    if names or _SPEC_FOLDER.search(command):
        what = ", ".join(names) or "the .spec folder"
        return (
            f"specforge: blocked a shell command that would reach hidden checks ({what}). "
            "Work from .spec/spec.md and .spec/plan.md."
        )
    if _SEARCH.search(command):
        dirs = _search_dirs(project)
        if not any(re.search(rf"(?<![\w.-]){re.escape(d)}(?![\w-])", command) for d in dirs):
            return (
                "specforge: blocked a search of the whole project, which would reach hidden "
                f"checks. Search inside {', '.join(dirs)} instead."
            )
    return None


def evaluate(event, project_dir):
    """Return None to allow the tool call, or a reason string to block it."""
    project = Path(project_dir).resolve()
    if not (project / ".spec").is_dir():
        return None  # specforge is not in use in this project

    tool = event.get("tool_name")
    if tool in READ_TOOLS:
        return _evaluate_read(event, project)
    if tool == "Bash":
        reason = _evaluate_bash(event, project)
        if reason:
            return reason
        tool_input = event.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        return _evaluate_hidden_bash(event, project, command) if isinstance(command, str) else None
    if tool == "Skill":
        return _evaluate_skill(event)
    if tool not in EDIT_TOOLS:
        return None

    try:
        parts = _target_parts(event, project)
    except ValueError:
        return "specforge: blocked an edit because the target file could not be determined."

    # M11: only the holdout writer writes holdout tests, and a session that may
    # build can't touch the hidden material.
    target = Path(event["tool_input"]["file_path"])
    if not target.is_absolute():
        target = Path(event.get("cwd") or project) / target
    folder = (project / holdout.HOLDOUT_DIR).resolve()
    if folder == target.resolve() or folder in target.resolve().parents:
        if event.get("agent_type") != holdout.WRITER:
            return (
                f"specforge: blocked edit to {holdout.relative(project, target)}. Only the "
                f"{holdout.WRITER} subagent writes holdout tests."
            )
        return None
    if _hidden_applies(event, project) and holdout.is_hidden(project, target):
        return (
            f"specforge: blocked edit to {holdout.relative(project, target)}. It holds hidden "
            "checks that a session which may build mustn't see."
        )
    if parts is None:
        return None

    if parts == (".spec", "state.json"):
        return (
            "specforge: .spec/state.json cannot be edited directly. "
            "Use /specforge:approve spec|plan to record an approval, or "
            "/specforge:unapprove spec|plan to withdraw one."
        )
    if parts[0] == ".spec":
        return None

    try:
        cfg = config.load(project)
    except config.ConfigError as e:
        return f"specforge: .spec/config.json is invalid ({e}). Fix it before editing code."

    needs = classify(parts, cfg)
    if needs == OPEN:
        return None

    rel = "/".join(parts)
    try:
        current = state.read(project)
    except state.StateMissingError:
        return (
            f"specforge: blocked edit to {rel}. .spec/ exists but state.json is missing. "
            "Run /specforge:spec to start the workflow."
        )
    except state.StateCorruptError as e:
        return (
            f"specforge: blocked edit to {rel}. .spec/state.json is corrupt ({e}). "
            "Fix or delete it, then run /specforge:spec."
        )

    if not current["spec_approved"]:
        return (
            f"specforge: blocked edit to {rel}. The spec is not approved yet. "
            "Review .spec/spec.md, then run /specforge:approve spec."
        )
    if needs == NEED_BOTH and not current["plan_approved"]:
        return (
            f"specforge: blocked edit to {rel}. The plan is not approved yet. "
            "Review .spec/plan.md, then run /specforge:approve plan."
        )
    if needs == NEED_BOTH and holdout.exists(project) and holdout.session_aware(project, event.get("session_id")):
        return (
            f"specforge: blocked edit to {rel}. This session has seen the hidden checks (it ran "
            "/specforge:spec, /plan or /review), so it can't write code while .spec/holdout.md "
            "has hidden ACs. Start a fresh session (/clear) and run /specforge:build there."
        )
    if needs == NEED_BOTH:
        reason = test_gate.red_block_reason(project, cfg, current)
        if reason:
            return f"specforge: blocked edit to {rel}. {reason}"
    return None


def evaluate_post(event, project_dir):
    """After spec.md changes post-approval, bump the version. Returns a message or None."""
    project = Path(project_dir).resolve()
    if event.get("tool_name") not in EDIT_TOOLS or not (project / ".spec").is_dir():
        return None
    try:
        parts = _target_parts(event, project)
        current = state.read(project)
    except (ValueError, state.StateMissingError, state.StateCorruptError):
        return None
    if parts != (".spec", "spec.md") or not current["spec_approved"]:
        return None
    new = state.bump_spec_version(project)
    return (
        f"specforge: .spec/spec.md changed after approval. Spec version is now "
        f"{new['spec_version']} and both approvals were reset. Review the change, then run "
        "/specforge:approve spec and /specforge:approve plan again."
    )


def main(argv=None, stdin=None) -> int:
    parser = argparse.ArgumentParser(prog="gate_check.py")
    parser.add_argument("--post", action="store_true")
    args = parser.parse_args(argv)
    stdin = stdin or sys.stdin

    try:
        event = json.loads(stdin.read())
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
    except ValueError:
        if args.post:
            return 0
        print("specforge: could not parse hook input; blocking to be safe.", file=sys.stderr)
        return 2

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()

    if args.post:
        try:
            message = evaluate_post(event, project_dir)
        except Exception as e:  # noqa: BLE001 - a PostToolUse hook cannot block anyway
            print(f"specforge: spec-change check failed ({e}).", file=sys.stderr)
            return 1
        if message:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": message,
                }
            }))
        return 0

    try:
        reason = evaluate(event, project_dir)
    except Exception as e:  # noqa: BLE001 - fail closed on any unexpected error
        print(f"specforge: gate check failed ({e}); blocking to be safe.", file=sys.stderr)
        return 2
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
