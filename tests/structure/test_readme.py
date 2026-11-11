import re
import shutil
import subprocess

import pytest

from conftest import headings, section

COMMANDS = ["spec", "plan", "build", "review", "status", "approve", "unapprove"]


@pytest.fixture(scope="module")
def readme(root):
    path = root / "README.md"
    assert path.is_file(), "missing README.md"
    return path.read_text()


@pytest.mark.parametrize(
    "title",
    ["install", "workflow", "commands", "gates", "configuration", "example session",
     "limitations", "development"],
)
def test_readme_has_section(readme, title):
    assert title in headings(readme)


def test_install_uses_the_real_marketplace_and_plugin_names(readme, plugin_json, marketplace_json):
    install = section(readme, "install")
    assert "/plugin marketplace add" in install
    assert f"/plugin install {plugin_json['name']}@{marketplace_json['name']}" in install


@pytest.mark.parametrize("name", COMMANDS)
def test_every_command_is_documented(readme, name):
    assert f"/specforge:{name}" in section(readme, "commands")


def test_workflow_has_a_text_diagram_of_all_phases(readme):
    wf = section(readme, "workflow")
    assert "```" in wf
    low = wf.lower()
    for phase in ("spec", "plan", "build", "review"):
        assert phase in low
    assert "approve" in low


def test_example_session_walks_through_every_command(readme):
    example = section(readme, "example session")
    for name in COMMANDS:
        assert f"/specforge:{name}" in example


def test_gates_section_names_each_hook_behavior(readme):
    low = section(readme, "gates").lower()
    for needle in ("spec_approved", "plan_approved", "test:", "spec_version", "state.json"):
        assert needle in low, needle


def test_configuration_documents_config_json_keys(readme):
    cfg = section(readme, "configuration")
    for key in ("code_dirs", "test_dirs", "test_command", ".spec/config.json", "source_fidelity",
                "red_first", "ac_budget"):
        assert key in cfg


def test_limitations_are_honest_about_what_hooks_cannot_enforce(readme):
    low = section(readme, "limitations").lower()
    assert "red" in low and "green" in low  # TDD order is not mechanically enforced
    assert "bash" in low  # shell writes to state.json are best effort


# ---------- step-by-step install and usage ----------

def test_install_explains_one_command_per_line(readme):
    low = section(readme, "install").lower()
    assert "one at a time" in low or "one per line" in low


def test_install_gives_shell_commands_and_verification(readme, plugin_json, marketplace_json):
    install = section(readme, "install")
    assert "claude plugin marketplace add" in install
    assert f"claude plugin install {plugin_json['name']}@{marketplace_json['name']}" in install
    assert "claude plugin list" in install
    assert "/reload-plugins" in install


def test_install_has_numbered_steps(readme):
    install = section(readme, "install")
    numbered = [l for l in install.splitlines() if l[:3] in ("1. ", "2. ", "3. ", "4. ")]
    assert len(numbered) >= 4


def test_usage_section_exists_and_precedes_workflow(readme):
    h = headings(readme)
    assert "usage step by step" in h
    assert h.index("usage step by step") < h.index("workflow")


def test_usage_prepares_a_project_first(readme):
    usage = section(readme, "usage step by step")
    for needle in ("git init", ".spec/config.json", "test_command"):
        assert needle in usage, needle


def test_usage_is_a_numbered_walkthrough_of_every_command(readme):
    usage = section(readme, "usage step by step")
    numbered = [l for l in usage.splitlines() if l.split(". ")[0].isdigit()]
    assert len(numbered) >= 10
    for name in COMMANDS:
        assert f"/specforge:{name}" in usage


def test_usage_tells_the_user_what_to_check_at_each_gate(readme):
    low = section(readme, "usage step by step").lower()
    assert "blocked" in low and "expect" in low


def test_troubleshooting_section(readme):
    assert "troubleshooting" in headings(readme)
    low = section(readme, "troubleshooting").lower()
    for needle in ("/plugin", "errors", "claude plugin validate", "/reload-plugins", "python3"):
        assert needle in low, needle


def test_readme_explains_red_first(readme):
    assert "red.py" in readme
    assert "red.py" in section(readme, "gates")


def test_readme_explains_that_a_hook_records_approvals(readme):
    # M5
    gates = section(readme, "gates")
    assert "UserPromptSubmit" in gates and "approve_hook.py" in readme
    assert "run `state.py approve` through Bash" not in readme


def test_readme_explains_that_withdrawn_approvals_keep_code_commits_blocked(readme):
    # M6-AC4
    gates = section(readme, "gates")
    assert "/specforge:unapprove" in gates
    assert "seven commands" in section(readme, "install")


def test_readme_explains_importing_spec_plus(readme):
    # M9
    assert "--from" in section(readme, "commands")
    assert "import_source.py" in readme


def test_readme_mentions_source_ids_in_plan_and_review(readme):
    # M10
    assert "trace_check.py" in readme


def test_readme_explains_holdout_compartments(readme):
    # M11
    gates = section(readme, "gates")
    assert "holdout.md" in gates and "/clear" in gates
    assert "holdout-writer" in readme


def test_readme_documents_the_holdout_command(readme):
    # M11-AC4
    assert "holdout_command" in section(readme, "configuration")


def test_readme_mentions_edge_case_coverage(readme):
    # M12
    assert "--edges" in readme and "EC-1" in readme


def test_readme_has_a_headless_section_with_exact_allow_rules(readme):
    # M13-AC3
    assert "headless / ci" in headings(readme)
    text = section(readme, "headless / ci")
    assert "--allowedTools" in text and "Bash(specforge:*)" in text and "dontAsk" in text
    for phase in ("spec", "approve", "plan", "build", "review"):
        assert f"/specforge:{phase}" in text
    assert "Bash(python3:*)" not in readme


# ---------- M14: the Headless / CI block as written ----------

_STUB = 'claude() { for a in "$@"; do printf "%s\\x1f" "$a"; done; printf "\\n"; }\n'


def _headless_block(readme):
    """Every ```bash block in the Headless section, joined, as one script."""
    blocks = re.findall(r"^```bash\n(.*?)^```", section(readme, "headless / ci"), re.DOTALL | re.MULTILINE)
    assert blocks, "the Headless section has no ```bash block"
    return "\n".join(blocks)


def _invocations(readme):
    """argv lists of each `claude` call in the Headless block, as bash splits them."""
    if shutil.which("bash") is None:
        pytest.skip("bash not installed")
    out = subprocess.run(["bash", "-c", _STUB + _headless_block(readme)],
                         capture_output=True, text=True, check=True).stdout
    return [line.split("\x1f")[:-1] for line in out.splitlines() if line]


def test_headless_block_passes_each_allow_rule_as_one_argument(readme):
    # M14-AC3: `--allowedTools $GIT` split "Bash(git checkout:*)" at the space,
    # so no git rule matched and a headless build couldn't create its branch
    calls = _invocations(readme)
    assert calls, "the Headless block runs no claude command"
    for argv in calls:
        for arg in argv:
            assert arg.count("(") == arg.count(")"), f"rule split by the shell: {arg!r} in {argv}"
    builds = [a for a in calls if any(x.startswith("/specforge:build") for x in a)]
    assert builds and all("Bash(git checkout:*)" in a and "Bash(git commit:*)" in a for a in builds)


def test_headless_block_shows_a_checkpoint_build_and_how_to_continue(readme):
    # M14-AC3
    calls = _invocations(readme)
    prompts = [a[a.index("-p") + 1] for a in calls if "-p" in a]
    assert "/specforge:build --mode checkpoint" in prompts
    assert "/specforge:build" in prompts  # run again for the next milestone
    assert any("--continue" in a for a in calls)
    text = section(readme, "headless / ci")
    assert "first unticked task" in text and "commits" in text


def test_readme_says_state_stays_in_the_working_folder(readme):
    # M14 known limit
    text = section(readme, "limitations")
    assert "state.json" in text and "fresh clone" in text


def test_readme_documents_the_folder_checks(readme):
    # M15
    assert "specforge config" in readme and "trace --tests" in readme
    configuration = section(readme, "configuration")
    assert "/specforge:plan" in configuration.split("`test_dirs`")[1].split("\n- ")[0]
