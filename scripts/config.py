"""Optional per-project settings in <project>/.spec/config.json."""
import json
from pathlib import Path

DEFAULT_CONFIG = {
    "code_dirs": ["src", "app", "lib"],
    "test_dirs": ["tests"],
    "test_command": None,
    # "commit": run the commit gate on exactly what would be committed.
    # "worktree": run it on the files on disk (the original behaviour).
    "commit_gate_scope": "commit",
    # Untracked paths the tests need (virtualenvs, installed packages). They are
    # linked into the temporary commit tree when present.
    "test_env_links": [".venv", "venv", "node_modules"],
    # Optional command that runs only some tests. When set, the commit gate runs
    # it with the AC-IDs of done tasks plus the current task, so each task can go
    # red -> green on its own. Placeholders: {ac_regex}, {pytest_k}.
    "test_filter": None,
    # The spec phase saves the description verbatim to .spec/source.md and
    # spec-reviewer checks the spec against it. false gives the original spec
    # phase. The command and agent prompts read this from config.json.
    "source_fidelity": True,
}

FILTER_PLACEHOLDERS = ("{ac_regex}", "{pytest_k}")

COMMIT_GATE_SCOPES = ("commit", "worktree")


class ConfigError(Exception):
    """config.json exists but is not valid."""


def _check_dirs(key, value):
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{key} must be a non-empty list of folder names")
    for name in value:
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            raise ConfigError(f"{key} entries must be plain folder names, got {name!r}")


def _check_links(value):
    if not isinstance(value, list):
        raise ConfigError("test_env_links must be a list of relative paths")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"test_env_links entries must be non-empty strings, got {item!r}")
        parts = item.replace("\\", "/").split("/")
        if item.startswith(("/", "\\")) or ".." in parts:
            raise ConfigError(f"test_env_links entries must stay inside the project, got {item!r}")


def load(project_dir) -> dict:
    cfg = {k: list(v) if isinstance(v, list) else v for k, v in DEFAULT_CONFIG.items()}
    path = Path(project_dir) / ".spec" / "config.json"
    if not path.is_file():
        return cfg
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        raise ConfigError(str(e)) from None
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    unknown = set(data) - set(DEFAULT_CONFIG)
    if unknown:
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")
    cfg.update(data)
    _check_dirs("code_dirs", cfg["code_dirs"])
    _check_dirs("test_dirs", cfg["test_dirs"])
    cmd = cfg["test_command"]
    if cmd is not None and (not isinstance(cmd, str) or not cmd.strip()):
        raise ConfigError("test_command must be a non-empty string")
    if cfg["commit_gate_scope"] not in COMMIT_GATE_SCOPES:
        raise ConfigError(f"commit_gate_scope must be one of {list(COMMIT_GATE_SCOPES)}")
    _check_links(cfg["test_env_links"])
    flt = cfg["test_filter"]
    if flt is not None and (
        not isinstance(flt, str) or not any(ph in flt for ph in FILTER_PLACEHOLDERS)
    ):
        raise ConfigError(
            f"test_filter must be a command containing one of {list(FILTER_PLACEHOLDERS)}"
        )
    if not isinstance(cfg["source_fidelity"], bool):
        raise ConfigError("source_fidelity must be true or false")
    return cfg
