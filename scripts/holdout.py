"""Hidden ACs and holdout tests, kept from the build by session (M11).

Hidden ACs (HAC-1, ...) live in .spec/holdout.md and their tests in
.spec/holdout/. A session that ran /specforge:spec, /plan or /review has seen
them ("aware"), so it may not write code. Any other session may not read the
hidden material: holdout.md, holdout/, source.md and requirement.md (which
hold the hidden checks' text), and the files source.md says were imported
with --from. None of this applies while no hidden ACs exist.
"""
import os
import re
from pathlib import Path

import state

WRITER = "specforge:holdout-writer"
HOLDOUT_FILE = Path(".spec") / "holdout.md"
HOLDOUT_DIR = Path(".spec") / "holdout"
HAC_BULLET = re.compile(r"^\s*[-*]\s+\**(?P<id>HAC-\d+)\**\s*(?:\[(?P<tags>[^\]]*)\]|\((?P<ptags>[^)]*)\))?\s*:")
_IMPORTED = re.compile(r"^## From `([^`]+)`\s*$", re.MULTILINE)
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}

# File types for Grep's `type` filter; an unknown type is assumed to match.
_TYPES = {
    "js": (".js", ".mjs", ".cjs", ".jsx"), "ts": (".ts", ".tsx", ".mts", ".cts"),
    "py": (".py", ".pyi"), "md": (".md", ".markdown"), "json": (".json",),
    "dart": (".dart",), "go": (".go",), "rust": (".rs",), "java": (".java",),
    "css": (".css",), "html": (".html", ".htm"), "yaml": (".yaml", ".yml"),
}


def hidden_acs(project):
    """HAC-ID -> its tags, from .spec/holdout.md."""
    path = Path(project) / HOLDOUT_FILE
    if not path.is_file():
        return {}
    acs = {}
    for line in path.read_text(errors="replace").splitlines():
        m = HAC_BULLET.match(line)
        if not m or m.group("id") in acs:
            continue
        tags = [t.strip() for t in (m.group("tags") or "").split(",") if t.strip()]
        if m.group("ptags") is not None:
            ptags = [t.strip() for t in m.group("ptags").split(",")]
            tags = ptags if all(re.match(r"^[A-Za-z]+-?\d+$", t) for t in ptags) else []
        acs[m.group("id")] = tags
    return acs


def exists(project) -> bool:
    """True while there are hidden ACs or holdout tests."""
    project = Path(project)
    if hidden_acs(project):
        return True
    folder = project / HOLDOUT_DIR
    return folder.is_dir() and any(p.is_file() for p in folder.rglob("*"))


def session_aware(project, session_id) -> bool:
    if not session_id:
        return False
    try:
        return session_id in state.read(project).get("aware_sessions", [])
    except (state.StateMissingError, state.StateCorruptError):
        return False


def hidden_files(project):
    """Every file holding hidden check text, as resolved paths."""
    project = Path(project).resolve()
    spec = project / ".spec"
    files = [spec / "holdout.md", spec / "source.md", spec / "requirement.md"]
    folder = project / HOLDOUT_DIR
    if folder.is_dir():
        files += [p for p in folder.rglob("*") if p.is_file()]
    source = spec / "source.md"
    if source.is_file():
        for name in _IMPORTED.findall(source.read_text(errors="replace")):
            path = Path(name)
            files.append(path if path.is_absolute() else project / path)
    return [p.resolve() for p in files]


def hidden_names(project):
    """Names that, in a shell command, point at hidden material."""
    names = {"holdout", "source.md", "requirement.md"}
    for path in hidden_files(project):
        if path.parent != (Path(project).resolve() / ".spec") and HOLDOUT_DIR.name not in path.parts:
            names.add(path.name)
    return names


def is_hidden(project, path) -> bool:
    path = Path(path).resolve()
    folder = (Path(project) / HOLDOUT_DIR).resolve()
    return path == folder or folder in path.parents or path in hidden_files(project)


def _glob_regex(pattern):
    """A glob as a regex over '/'-separated paths: ** spans folders, * doesn't."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def _expand_braces(pattern):
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    return [x for alt in m.group(1).split(",")
            for x in _expand_braces(pattern[:m.start()] + alt + pattern[m.end():])]


def _matches(pattern, rel):
    rel = rel.replace(os.sep, "/")
    for p in _expand_braces(pattern):
        target = rel if "/" in p else rel.rsplit("/", 1)[-1]
        if _glob_regex(p).match(target):
            return True
    return False


def _in_scope(project, search_path, cwd):
    base = Path(search_path or cwd or project)
    if not base.is_absolute():
        base = Path(cwd or project) / base
    return base.resolve()


def grep_reaches(project, tool_input, cwd=None):
    """Hidden files a Grep with this input could read."""
    base = _in_scope(project, tool_input.get("path"), cwd)
    glob, kind = tool_input.get("glob"), tool_input.get("type")
    hits = []
    for f in hidden_files(project):
        if not f.is_file() or not (f == base or base in f.parents):
            continue
        rel = str(f.relative_to(base)) if f != base else f.name
        if glob and not _matches(glob, rel):
            continue
        if kind and kind in _TYPES and not f.name.endswith(_TYPES[kind]):
            continue
        hits.append(f)
    return hits


def glob_reaches(project, tool_input, cwd=None):
    """Hidden files a Glob with this input would list. A Glob pattern matches the
    whole path below its base, so "*.md" means files directly in the base."""
    base = _in_scope(project, tool_input.get("path"), cwd)
    patterns = [_glob_regex(p) for p in _expand_braces(tool_input.get("pattern") or "*")]
    hits = []
    for f in hidden_files(project):
        if f.is_file() and base in f.parents:
            rel = str(f.relative_to(base)).replace(os.sep, "/")
            if any(p.match(rel) for p in patterns):
                hits.append(f)
    return hits


def relative(project, path):
    try:
        return str(Path(path).resolve().relative_to(Path(project).resolve()))
    except ValueError:
        return str(path)
