"""M1: warn when Claude Code is too old for onFailure: block."""
import subprocess
import sys
from pathlib import Path

import pytest

import version_check

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "version_check.py"


# ---------- reading the version ----------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("claude-code_2-1-321_agent", (2, 1, 321)),
        ("claude-code_2-1-289_agent", (2, 1, 289)),
        ("claude-code_10-0-1", (10, 0, 1)),
        ("cursor_1-2-3_agent", None),
        ("claude-code_agent", None),
        ("", None),
    ],
)
def test_version_from_ai_agent(value, expected):
    assert version_check.from_ai_agent(value) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2.1.321 (Claude Code)\n", (2, 1, 321)),
        ("2.1.289", (2, 1, 289)),
        ("Claude Code\n", None),
        ("", None),
    ],
)
def test_version_from_version_output(text, expected):
    assert version_check.from_version_output(text) == expected


def test_detect_prefers_ai_agent_and_does_not_run_anything():
    def run(*a, **k):
        raise AssertionError("must not run the binary when AI_AGENT has the version")
    env = {"AI_AGENT": "claude-code_2-1-300_agent", "CLAUDE_CODE_EXECPATH": "/x/claude"}
    assert version_check.detect(env, run=run) == (2, 1, 300)


def test_detect_falls_back_to_asking_the_running_binary(tmp_path):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\necho '2.1.290 (Claude Code)'\n")
    exe.chmod(0o755)
    assert version_check.detect({"CLAUDE_CODE_EXECPATH": str(exe)}) == (2, 1, 290)


def test_detect_runs_the_binary_without_a_shell_and_with_a_timeout(tmp_path):
    exe = tmp_path / "claude"
    exe.write_text("")
    exe.chmod(0o755)
    seen = {}
    def run(args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return subprocess.CompletedProcess(args, 0, stdout="2.1.295 (Claude Code)\n", stderr="")
    assert version_check.detect({"CLAUDE_CODE_EXECPATH": str(exe)}, run=run) == (2, 1, 295)
    assert seen["args"] == [str(exe), "--version"]
    assert not seen["kwargs"].get("shell") and seen["kwargs"].get("timeout")


@pytest.mark.parametrize("problem", ["missing", "crashes", "hangs", "garbage"])
def test_detect_returns_none_when_the_binary_cannot_tell(tmp_path, problem):
    exe = tmp_path / "claude"
    if problem != "missing":
        exe.write_text("")
        exe.chmod(0o755)
    def run(args, **kwargs):
        if problem == "crashes":
            raise OSError("exec format error")
        if problem == "hangs":
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        return subprocess.CompletedProcess(args, 0, stdout="no version here", stderr="")
    assert version_check.detect({"CLAUDE_CODE_EXECPATH": str(exe)}, run=run) is None


def test_detect_returns_none_with_nothing_to_go_on():
    assert version_check.detect({}) is None


# ---------- the message ----------

def test_older_than_the_minimum_warns_that_crashing_hooks_fail_open():
    msg = version_check.message((2, 1, 289))
    assert "2.1.289" in msg and "2.1.295" in msg
    assert "crash" in msg and "let the action through" in msg
    assert "\n" not in msg  # one line


@pytest.mark.parametrize("version", [(2, 1, 295), (2, 1, 321), (2, 2, 0), (3, 0, 0)])
def test_minimum_or_newer_prints_nothing(version):
    assert version_check.message(version) is None


def test_unknown_version_says_so_and_names_the_minimum():
    msg = version_check.message(None)
    assert "couldn't determine" in msg and "2.1.295" in msg
    assert "\n" not in msg


# ---------- running the script ----------

def run_script(env):
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)


def test_script_prints_the_warning_and_exits_zero_on_an_old_version():
    result = run_script({"AI_AGENT": "claude-code_2-1-289_agent"})
    assert result.returncode == 0
    assert "2.1.289" in result.stdout and result.stdout.count("\n") == 1


def test_script_prints_nothing_on_a_new_version():
    result = run_script({"AI_AGENT": "claude-code_2-1-321_agent"})
    assert result.returncode == 0 and result.stdout == ""
