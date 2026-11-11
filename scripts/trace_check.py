#!/usr/bin/env python3
"""Carry the source check IDs through the plan and the review (M10).

    trace_check.py --plan     check plan.md's traceability Source column against spec.md
    trace_check.py --rollup   print review.md's per-check roll-up as a markdown table

An AC's tags, and the source's checks, are read with spec_size.py's rules (the
ones tools/check_coverage.py uses), so every script agrees on what a tag is.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import spec_size

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
        if line.startswith("|") and _cells(line)[:1] and _cells(line)[0].lower() == "ac":
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
    if problems:
        return 1, "The traceability table doesn't match .spec/spec.md:\n" + "\n".join(f"- {p}" for p in problems)
    return 0, f"The traceability table matches .spec/spec.md: {len(expected)} ACs, each with its source tags."


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

    found = _find(review, "Status")
    if found is None:
        return 1, "review.md's coverage table has no Status column."
    col, width, rows = found
    statuses = {}
    for row in rows:
        if row and re.fullmatch(r"AC-\d+", row[0].replace("*", "")):
            # Status is usually last and code cells may hold stray pipes, so count from the right
            statuses[row[0].replace("*", "")] = _status(row[col - width] if len(row) >= width - col else "")

    tags = spec_size._ac_tags(spec)
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="trace_check.py")
    parser.add_argument("--project-dir")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--rollup", action="store_true")
    args = parser.parse_args(argv)
    project = args.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    code, message = check_plan(project) if args.plan else rollup(project)
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
