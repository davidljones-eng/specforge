# Review: <feature name>

Spec version: <n>

## Acceptance Criteria Coverage
| AC | Code | Test | Status |
|----|------|------|--------|
| AC-1 | <file:line> | <test file::test name> | pass / fail / missing |

## Holdout
<Only if `.spec/holdout.md` lists hidden ACs. The holdout tests' results from `holdout_command`, one row per hidden AC. Otherwise write "No hidden ACs.">

| HAC | Test | Status |
|-----|------|--------|
| HAC-1 | <.spec/holdout/file::test name> | pass / fail / missing |

## Source Checks
<The output of `scripts/trace_check.py --rollup`, as is: one row per source check with its ACs and pass, fail, missing or out of scope.>

## Gaps Found
- <Missing tests, uncovered edge cases, spec drift. Write "None" if there are none.>

## Test Results
```
<full test run output>
```

## Pull Request
<PR link. Never merged by this plugin.>
