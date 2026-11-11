#!/usr/bin/env python3
"""Carry the source check IDs through the plan and the review (M10).

    trace_check.py --plan     check plan.md's traceability Source column against spec.md,
                              and warn about planned folders the hooks don't gate (M15)
    trace_check.py --tests    check every AC has a test where the hooks look for it (M15)
    trace_check.py --rollup   print review.md's per-check roll-up as a markdown table
    trace_check.py --edges    check review.md has a row for every EC- edge case in spec.md (M12)

An AC's tags, and the source's checks, are read with spec_size.py's rules (the
ones tools/check_coverage.py uses), so every script agrees on what a tag is.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import config
import holdout
import spec_size
import test_gate

_NONE = {"", "—", "–", "-", "none", "n/a"}
_C_ID = re.compile(r"^C\d+$")


def _cells(line):
    """Cells of a markdown table row; a pipe escaped as \\| stays in its cell."""
    parts = re.split(r"(?<!\\)\|", line.strip())
    return [c.strip() for c in parts[1:-1]]


def _ac_tables(text):
    """(header, rows) for every table whose first header cell is "AC"."""
    tables, lines = [], text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and _cells(line)[:1] and _cells(line)[0].lower() in ("ac", "hac", "ec"):
            header, rows = _cells(line), []
            i += 1
            if i < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i].strip()):
                i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            tables.append((header, rows))
            continue
        i += 1
    return tables


def _find(text, column):
    """(index of `column`, rows) in the first AC table that has it, or None."""
    for header, rows in _ac_tables(text):
        names = [h.lower() for h in header]
        if column.lower() in names:
            return names.index(column.lower()), len(header), rows
    return None


def _tags(cell):
    cell = cell.replace("`", "").strip().strip("[]()").strip()
    if cell.lower() in _NONE:
        return []
    return [t.strip().strip("[]()") for t in cell.split(",") if t.strip()]


def _read(project, name):
    path = Path(project) / ".spec" / name
    if not path.is_file():
        raise FileNotFoundError(f"No .spec/{name} yet.")
    return path.read_text(errors="replace")


def check_plan(project):
    """(0, message) if plan.md's Source column matches spec.md's tags, else (1, problems)."""
    try:
        spec, plan = _read(project, "spec.md"), _read(project, "plan.md")
    except FileNotFoundError as e:
        return 1, str(e)
    expected = spec_size._ac_tags(spec)
    found = _find(plan, "Source")
    if found is None:
        return 1, ("plan.md's traceability table has no Source column. Add one after AC, "
                   "with each AC's tags as written in spec.md (\"—\" for none).")
    col, width, rows = found
    listed = {}
    for row in rows:
        if row and re.fullmatch(r"AC-\d+", row[0].replace("*", "")):
            # Source sits near the start, so count from the left
            listed[row[0].replace("*", "")] = _tags(row[col] if col < len(row) else "")

    problems = []
    for ac, tags in expected.items():
        if ac not in listed:
            problems.append(f"{ac} is in spec.md but not in the traceability table.")
        elif set(listed[ac]) != set(tags):
            have = ", ".join(listed[ac]) or "—"
            want = ", ".join(tags) or "—"
            problems.append(f"{ac}: Source says {have}, but spec.md tags it {want}.")
    for ac in listed:
        if ac not in expected:
            problems.append(f"{ac} is in the traceability table but not in spec.md.")
    for hac in sorted(set(re.findall(r"\bHAC-\d+\b", plan)), key=lambda h: int(h[4:])):
        problems.append(f"plan.md mentions {hac}, a hidden AC. Hidden ACs stay out of the plan, which the build reads.")
    note = _ungated_note(project, plan)
    if problems:
        return 1, ("The traceability table doesn't match .spec/spec.md:\n"
                   + "\n".join(f"- {p}" for p in problems) + note)
    return 0, f"The traceability table matches .spec/spec.md: {len(expected)} ACs, each with its source tags.{note}"


def _section_lines(text, title):
    lines, level = [], None
    for line in text.splitlines():
        heading = re.match(r"^(#+)\s*(.*?)\s*$", line)
        if heading:
            if level is not None and len(heading.group(1)) <= level:
                break
            if heading.group(2).lower() == title:
                level = len(heading.group(1))
            continue
        if level is not None:
            lines.append(line)
    return lines


def _ungated_note(project, plan):
    """A note naming Folder Structure entries outside code_dirs and test_dirs (M15-AC3)."""
    try:
        cfg = config.load(project)
    except config.ConfigError as e:
        return f"\n\nThe planned folders weren't checked: .spec/config.json is invalid ({e})."
    known = set(cfg["code_dirs"]) | set(cfg["test_dirs"])
    ungated = []
    for line in _section_lines(plan, "folder structure"):
        m = re.match(r"^\s*[-*]\s+`([^`]+)`", line)
        if not m or m.group(1).startswith(".spec"):
            continue
        path = m.group(1).strip()
        parts = [x for x in path.strip("/").split("/") if x]
        folders = parts if path.endswith("/") else parts[:-1]
        if parts and not any(f in known for f in folders):
            ungated.append(path)
    if not ungated:
        return ""
    return (
        f"\n\nNote: the hooks don't gate {', '.join(ungated)}. They're outside code_dirs "
        f"({test_gate.folder_list(cfg['code_dirs'])}) and test_dirs ({test_gate.folder_list(cfg['test_dirs'])}), "
        "so code there skips the approval and RED checks, and tests there aren't found. "
        "If they hold code or tests, plan them under those folders, or ask the user to add "
        "the folder to code_dirs or test_dirs in .spec/config.json."
    )


# Test files by name: a.test.js, a.spec.ts, test_a.py, a_test.go
_TEST_FILE = re.compile(r"(?:\.(?:test|spec)\.[A-Za-z0-9]+|^test_.+\.py|_test\.[A-Za-z0-9]+)$")
_SKIP = test_gate._SKIP_DIRS | {".spec"}


def _test_files(project):
    for root, dirs, files in os.walk(project, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP)
        for name in sorted(files):
            if _TEST_FILE.search(name):
                yield (Path(root) / name).relative_to(project).as_posix()


def check_tests(project):
    """(0, summary) if every AC in spec.md has a test under test_dirs and no test
    named after one sits elsewhere, else (1, problems). Run after the plan phase
    writes its tests: the build's commit gate and red look only in test_dirs."""
    project = Path(project)
    try:
        spec = _read(project, "spec.md")
    except FileNotFoundError as e:
        return 1, str(e)
    try:
        cfg = config.load(project)
    except config.ConfigError as e:
        return 1, f".spec/config.json is invalid ({e})."
    test_dirs = cfg["test_dirs"]
    where = test_gate.folder_list(test_dirs)
    acs = list(spec_size._ac_tags(spec))
    missing, misplaced = [], {}
    for ac in acs:
        inside, outside = test_gate.tests_for(project, test_dirs, ac)
        if not inside:
            missing.append(ac)
        for f in outside:
            misplaced.setdefault(f, []).append(ac)
    others = [f for f in _test_files(project)
              if not test_gate.in_test_dirs(f, test_dirs) and f not in misplaced]
    other_note = (f"\n\nOther test files outside the test folders, which the hooks don't see: "
                  f"{', '.join(others)}." if others else "")

    problems = []
    for f, ids in sorted(misplaced.items()):
        problems.append(f"{f} has tests for {', '.join(ids)}, but it's outside the test folders ({where}).")
    # an AC whose only tests are misplaced is already named in the lines above
    problems += [f"{ac} has no test named after it." for ac in missing
                 if not any(ac in ids for ids in misplaced.values())]
    if problems:
        return 1, (
            f"The tests aren't all where the hooks look for them ({where}), so the build "
            "couldn't record RED or commit code for these ACs:\n"
            + "\n".join(f"- {p}" for p in problems)
            + f"\nMove the tests under {test_dirs[0]}/, or ask the user to add their folder to "
            "test_dirs in .spec/config.json. Don't edit .spec/config.json yourself."
            + other_note
        )
    return 0, f"Every AC in spec.md has a test under the test folders ({where}): {len(acs)} ACs.{other_note}"


def _status(cell):
    low = cell.lower()
    for word in ("fail", "missing", "pass"):
        if word in low:
            return word
    return "missing"


def rollup(project):
    """(0, markdown table) with one row per source check, or (1, reason)."""
    try:
        spec, review = _read(project, "spec.md"), _read(project, "review.md")
    except FileNotFoundError as e:
        return 1, str(e)
    source_path = Path(project) / ".spec" / "source.md"
    source = source_path.read_text(errors="replace") if source_path.is_file() else ""
    checks = sorted({i for i in spec_size._table_ids(source) if _C_ID.match(i)}, key=spec_size._number)
    if not checks:
        return 0, "No source checks in .spec/source.md, so there is nothing to roll up."

    # The AC coverage table, and the Holdout table for hidden ACs (M11)
    statuses, seen_status = {}, False
    for header, rows in _ac_tables(review):
        names = [h.lower() for h in header]
        if "status" not in names:
            continue
        seen_status = True
        col, width = names.index("status"), len(header)
        for row in rows:
            ac = row[0].replace("*", "") if row else ""
            if re.fullmatch(r"H?AC-\d+", ac):
                # Status is usually last and code cells may hold stray pipes, so count from the right
                statuses[ac] = _status(row[col - width] if len(row) >= width - col else "")
    if not seen_status:
        return 1, "review.md's coverage table has no Status column."

    tags = {**spec_size._ac_tags(spec), **holdout.hidden_acs(project)}
    out_of_scope = spec_size._out_of_scope_ids(spec)
    lines = ["| Check | ACs | Status |", "|---|---|---|"]
    for check in checks:
        acs = [ac for ac, ts in tags.items() if check in ts]
        if not acs:
            status = "out of scope" if check in out_of_scope else "missing"
        else:
            results = [statuses.get(ac, "missing") for ac in acs]
            status = "fail" if "fail" in results else "missing" if "missing" in results else "pass"
        lines.append(f"| {check} | {', '.join(acs) or '—'} | {status} |")
    return 0, "\n".join(lines)


_EC_BULLET = re.compile(r"^\s*[-*]\s+\**(EC-\d+)\**\s*:", re.MULTILINE)


def _edge_status(cell):
    low = cell.replace("`", "").replace("*", "").strip().lower()
    if "untested" in low:
        return "untested"
    if "review only" in low or "review-only" in low:
        return "review only"
    if low.startswith("tested"):
        return "tested"
    return None


def check_edges(project):
    """(0, summary) if review.md has a valid row for every EC- edge case in spec.md."""
    try:
        spec, review = _read(project, "spec.md"), _read(project, "review.md")
    except FileNotFoundError as e:
        return 1, str(e)
    ids = list(dict.fromkeys(_EC_BULLET.findall(spec)))
    if not ids:
        return 0, ("spec.md has no EC-IDs (it was written before M12), so there's nothing to "
                   "check. The review numbers the edge cases itself.")
    table = None
    for header, rows in _ac_tables(review):
        names = [h.lower() for h in header]
        if names[0] == "ec" and "status" in names:
            table = (names.index("status"), len(header), rows)
            break
    if table is None:
        return 1, "review.md has no Edge Cases table (first column EC, with a Status column)."
    col, width, rows = table
    found = {}
    for row in rows:
        ec = row[0].replace("*", "") if row else ""
        if re.fullmatch(r"EC-\d+", ec):
            found[ec] = _edge_status(row[col - width] if len(row) >= width - col else "")
    problems = [f"{ec} is in spec.md but has no row in the Edge Cases table." for ec in ids if ec not in found]
    problems += [f"{ec}: the status must be tested, untested or review only." for ec in ids
                 if ec in found and found[ec] is None]
    problems += [f"{ec} is in the Edge Cases table but not in spec.md." for ec in found if ec not in ids]
    if problems:
        return 1, "The Edge Cases table doesn't match .spec/spec.md:\n" + "\n".join(f"- {p}" for p in problems)
    counts = {s: sum(1 for ec in ids if found[ec] == s) for s in ("tested", "untested", "review only")}
    return 0, (f"Every edge case has a row: {len(ids)} edge cases, {counts['tested']} tested, "
               f"{counts['untested']} untested, {counts['review only']} review only.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="trace_check.py")
    parser.add_argument("--project-dir")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--rollup", action="store_true")
    mode.add_argument("--edges", action="store_true")
    mode.add_argument("--tests", action="store_true")
    args = parser.parse_args(argv)
    project = args.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if args.plan:
        code, message = check_plan(project)
    elif args.rollup:
        code, message = rollup(project)
    elif args.tests:
        code, message = check_tests(project)
    else:
        code, message = check_edges(project)
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
