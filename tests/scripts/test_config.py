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
