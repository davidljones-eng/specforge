import json

import pytest

import config


def write(proj, text):
    (proj / ".spec").mkdir(exist_ok=True)
    (proj / ".spec" / "config.json").write_text(text)


def test_defaults_when_no_config_file(tmp_path):
    assert config.load(tmp_path) == {
        "code_dirs": ["src", "app", "lib"],
        "test_dirs": ["tests"],
        "test_command": None,
        "commit_gate_scope": "commit",
        "test_env_links": [".venv", "venv", "node_modules"],
        "test_filter": None,
        "source_fidelity": True,
        "red_first": True,
        "ac_budget": 15,
        "holdout_command": None,
    }


def test_defaults_are_not_shared_between_calls(tmp_path):
    config.load(tmp_path)["code_dirs"].append("x")
    assert config.load(tmp_path)["code_dirs"] == ["src", "app", "lib"]


def test_overrides_are_merged_over_defaults(tmp_path):
    write(tmp_path, json.dumps({"code_dirs": ["backend"], "test_command": "make test"}))
    cfg = config.load(tmp_path)
    assert cfg["code_dirs"] == ["backend"]
    assert cfg["test_command"] == "make test"
    assert cfg["test_dirs"] == ["tests"]


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{bad",
        "[]",
        '{"code_dirs": "src"}',
        '{"code_dirs": [1]}',
        '{"code_dirs": ["a/b"]}',  # directory names only, not paths
        '{"test_dirs": []}',
        '{"test_command": 5}',
        '{"test_command": ""}',
        '{"unknown": 1}',
    ],
)
def test_invalid_config_raises(tmp_path, raw):
    write(tmp_path, raw)
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)


# ---------- commit gate settings ----------

def test_commit_gate_defaults(tmp_path):
    cfg = config.load(tmp_path)
    assert cfg["commit_gate_scope"] == "commit"
    assert cfg["test_env_links"] == [".venv", "venv", "node_modules"]


@pytest.mark.parametrize("scope", ["commit", "worktree"])
def test_commit_gate_scope_accepts_known_values(tmp_path, scope):
    write(tmp_path, json.dumps({"commit_gate_scope": scope}))
    assert config.load(tmp_path)["commit_gate_scope"] == scope


def test_commit_gate_scope_rejects_unknown_values(tmp_path):
    write(tmp_path, json.dumps({"commit_gate_scope": "staged"}))
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)


@pytest.mark.parametrize("links", ["envdir", [""], ["/abs"], ["../up"], ["a/../../b"], [3]])
def test_test_env_links_must_be_relative_paths_inside_the_project(tmp_path, links):
    write(tmp_path, json.dumps({"test_env_links": links}))
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)


def test_test_env_links_may_be_empty_or_nested(tmp_path):
    write(tmp_path, json.dumps({"test_env_links": []}))
    assert config.load(tmp_path)["test_env_links"] == []
    write(tmp_path, json.dumps({"test_env_links": ["tools/bin"]}))
    assert config.load(tmp_path)["test_env_links"] == ["tools/bin"]


# ---------- per-task test scope ----------

def test_test_filter_defaults_to_none(tmp_path):
    assert config.load(tmp_path)["test_filter"] is None


@pytest.mark.parametrize(
    "value", ["node --test --test-name-pattern {ac_regex}", "pytest -q -k {pytest_k}"]
)
def test_test_filter_accepts_a_template_with_a_placeholder(tmp_path, value):
    write(tmp_path, json.dumps({"test_filter": value}))
    assert config.load(tmp_path)["test_filter"] == value


@pytest.mark.parametrize("value", ["node --test", "", 3, ["x"]])
def test_test_filter_must_be_a_string_with_a_placeholder(tmp_path, value):
    write(tmp_path, json.dumps({"test_filter": value}))
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)


@pytest.mark.parametrize(
    "value",
    [
        'node --test --test-name-pattern "{ac_regex}"',
        "node --test --test-name-pattern '{ac_regex}'",
        'node --test --test-name-pattern "^{ac_regex}"',
        'pytest -q -k "{pytest_k} and not slow"',
        'npx jest -t "{ac_regex}" --ci',
    ],
)
def test_test_filter_rejects_a_placeholder_inside_quotes(tmp_path, value):
    # The gate inserts the value already quoted. Extra quotes become part of
    # the pattern, which then matches no test, so every commit would pass.
    write(tmp_path, json.dumps({"test_filter": value}))
    with pytest.raises(config.ConfigError, match="quotes"):
        config.load(tmp_path)


@pytest.mark.parametrize(
    "value",
    [
        "node --test --test-name-pattern={ac_regex}",
        'node --test --test-name-pattern {ac_regex} "tests/*.test.js"',
        "node --test --test-name-pattern {ac_regex} 'it'\''s'",
        'npx jest -t {ac_regex} --testPathPattern "tests/"',
    ],
)
def test_test_filter_allows_quotes_elsewhere_in_the_command(tmp_path, value):
    write(tmp_path, json.dumps({"test_filter": value}))
    assert config.load(tmp_path)["test_filter"] == value


# ---------- source fidelity (M7) ----------

def test_source_fidelity_is_on_by_default(tmp_path):
    assert config.load(tmp_path)["source_fidelity"] is True


@pytest.mark.parametrize("value", [True, False])
def test_source_fidelity_accepts_true_or_false(tmp_path, value):
    write(tmp_path, json.dumps({"source_fidelity": value}))
    assert config.load(tmp_path)["source_fidelity"] is value


@pytest.mark.parametrize("value", ["false", 0, 1, None, []])
def test_source_fidelity_must_be_a_boolean(tmp_path, value):
    write(tmp_path, json.dumps({"source_fidelity": value}))
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)


# ---------- red first (M4) ----------

def test_red_first_is_on_by_default(tmp_path):
    assert config.load(tmp_path)["red_first"] is True


@pytest.mark.parametrize("value", ["false", 0, None])
def test_red_first_must_be_a_boolean(tmp_path, value):
    write(tmp_path, json.dumps({"red_first": value}))
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)


# ---------- AC budget (M8) ----------

@pytest.mark.parametrize("value", [1, 40, None])
def test_ac_budget_accepts_a_positive_integer_or_null(tmp_path, value):
    write(tmp_path, json.dumps({"ac_budget": value}))
    assert config.load(tmp_path)["ac_budget"] == value


@pytest.mark.parametrize("value", [0, -3, 2.5, "15", True, [15]])
def test_ac_budget_rejects_anything_else(tmp_path, value):
    write(tmp_path, json.dumps({"ac_budget": value}))
    with pytest.raises(config.ConfigError, match="ac_budget"):
        config.load(tmp_path)


# ---------- holdout command (M11-AC4) ----------

@pytest.mark.parametrize("value", ["node --test .spec/holdout/*.test.js", None])
def test_holdout_command_is_a_command_or_null(tmp_path, value):
    write(tmp_path, json.dumps({"holdout_command": value}))
    assert config.load(tmp_path)["holdout_command"] == value


@pytest.mark.parametrize("value", ["", "   ", 5, ["node"]])
def test_holdout_command_rejects_anything_else(tmp_path, value):
    write(tmp_path, json.dumps({"holdout_command": value}))
    with pytest.raises(config.ConfigError, match="holdout_command"):
        config.load(tmp_path)


# ---------- specforge config (M15-AC1) ----------

def test_main_prints_the_settings_with_defaults_filled_in(tmp_path, monkeypatch, capsys):
    (tmp_path / ".spec").mkdir()
    write(tmp_path, '{"test_dirs": ["test"]}')
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert config.main([]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["test_dirs"] == ["test"] and got["code_dirs"] == ["src", "app", "lib"]
    assert set(got) == set(config.DEFAULT_CONFIG)


def test_main_without_a_config_file_prints_the_defaults(tmp_path, capsys):
    assert config.main(["--project-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == config.DEFAULT_CONFIG


def test_main_reports_an_invalid_config(tmp_path, capsys):
    (tmp_path / ".spec").mkdir()
    write(tmp_path, '{"test_dirs": []}')
    assert config.main(["--project-dir", str(tmp_path)]) == 1
    assert "test_dirs" in capsys.readouterr().out
