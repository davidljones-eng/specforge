#!/usr/bin/env python3
"""Warn when Claude Code is too old for the hooks' onFailure: block.

Before 2.1.295, a hook script that crashes lets the action through instead of
blocking it. /specforge:status runs this first and shows whatever it prints.

Claude Code has no documented way to tell a command its version, so this reads
two undocumented variables it sets for tool calls: AI_AGENT
("claude-code_2-1-321_agent") and, failing that, CLAUDE_CODE_EXECPATH (the
running binary, asked for --version). If neither works, it says so rather than
guessing. It always exits 0: status is read-only and must never fail on this.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

MINIMUM = (2, 1, 295)
TIMEOUT = 5

_AI_AGENT = re.compile(r"^claude-code_(\d+)-(\d+)-(\d+)(?:_|$)")
_VERSION = re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b")


def _fmt(version):
    return ".".join(str(n) for n in version)


def from_ai_agent(value):
    match = _AI_AGENT.match(value or "")
    return tuple(int(n) for n in match.groups()) if match else None


def from_version_output(text):
    match = _VERSION.search(text or "")
    return tuple(int(n) for n in match.groups()) if match else None


def detect(env, run=subprocess.run):
    """The running Claude Code version as (major, minor, patch), or None."""
    version = from_ai_agent(env.get("AI_AGENT", ""))
    if version:
        return version
    exe = env.get("CLAUDE_CODE_EXECPATH", "")
    if not exe or not Path(exe).is_file():
        return None
    try:
        result = run([exe, "--version"], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return from_version_output(result.stdout)


def message(version):
    """One line to show the user, or None when there is nothing to say."""
    if version is None:
        return (
            "specforge: couldn't determine the Claude Code version; 2.1.295 or later is "
            "required so that a crashing hook blocks the action instead of letting it through."
        )
    if version < MINIMUM:
        return (
            f"specforge: warning: Claude Code {_fmt(version)} is older than {_fmt(MINIMUM)}, "
            "so a hook script that crashes will let the action through instead of blocking it. "
            "Run `claude update`."
        )
    return None


def main():
    try:
        line = message(detect(os.environ))
    except Exception:  # noqa: BLE001 - never break /specforge:status
        line = message(None)
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
