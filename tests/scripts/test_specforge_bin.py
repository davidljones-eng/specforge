"""M13: the specforge entry point, run the way the shell runs it."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import state

BIN = Path(__file__).resolve().parents[2] / "bin" / "specforge"


def sf(proj, *args):
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR")}
    return subprocess.run([str(BIN), *args], cwd=proj, env=env, capture_output=True, text=True, timeout=60)


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "a project with spaces"
    p.mkdir()
    return p


def test_the_entry_point_is_an_executable_python_script():
    assert os.access(BIN, os.X_OK)
    assert BIN.read_text().startswith("#!/usr/bin/env python3")


def test_state_init_get_and_set_phase(proj):
    assert sf(proj, "state", "init").returncode == 0
    assert sf(proj, "state", "set-phase", "plan").returncode == 0
    got = sf(proj, "state", "get")
    assert got.returncode == 0 and json.loads(got.stdout)["phase"] == "plan"


@pytest.mark.parametrize("args", [["state", "approve", "spec"], ["state", "unapprove", "plan"], ["approve", "spec"], ["unapprove", "spec"]])
def test_there_is_no_way_to_approve(proj, args):
    sf(proj, "state", "init")
    result = sf(proj, *args)
    assert result.returncode != 0
    assert state.read(proj)["spec_approved"] is False


def test_the_other_scripts_are_reachable(proj):
    sf(proj, "state", "init")
    (proj / ".spec" / "spec.md").write_text("## Acceptance Criteria\n- AC-1: a\n")
    (proj / "doc.md").write_text("hello\n")
    assert "1 AC" in sf(proj, "spec-size").stdout
    assert sf(proj, "import", "doc.md").returncode == 0
    assert "hello" in (proj / ".spec" / "source.md").read_text()
    assert "no EC-IDs" in sf(proj, "trace", "--edges").stdout or sf(proj, "trace", "--edges").returncode == 1
    red = sf(proj, "red")
    assert red.returncode in (0, 1) and red.stdout.strip()
    assert sf(proj, "version-check").returncode == 0


@pytest.mark.parametrize("args", [[], ["nonsense"], ["--help"]])
def test_usage(proj, args):
    result = sf(proj, *args)
    assert "specforge state" in result.stdout + result.stderr
    assert result.returncode == (0 if args == ["--help"] else 2)


def test_config_prints_the_settings_with_defaults(proj):
    # M15-AC1
    got = sf(proj, "config")
    assert got.returncode == 0
    assert json.loads(got.stdout)["test_dirs"] == ["tests"]


def test_usage_lists_config_and_trace_tests(proj):
    out = sf(proj, "--help").stdout
    assert "specforge config" in out and "--tests" in out


def test_usage_lists_red_all(proj):
    # M16-AC2
    assert "red [--all]" in sf(proj, "--help").stdout


def test_test_runs_the_configured_command_and_passes_its_status(proj):
    # M18-AC1, run the way the shell runs it
    (proj / ".spec").mkdir()
    cmd = f"{sys.executable} -c \"import sys; print('suite ran'); sys.exit(3)\""
    (proj / ".spec" / "config.json").write_text(json.dumps({"test_command": cmd}))
    got = sf(proj, "test")
    assert got.returncode == 3
    lines = got.stdout.splitlines()
    assert lines[0] == f"specforge test: {cmd}" and "suite ran" in lines[1:]


def test_usage_lists_test(proj):
    # M18-AC1
    assert "specforge test [AC-ID ...]" in sf(proj, "--help").stdout
