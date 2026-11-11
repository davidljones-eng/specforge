# Review: <feature name>

Spec version: <n>

## Acceptance Criteria Coverage
| AC | Code | Test | Status |
|----|------|------|--------|
| AC-1 | <file:line> | <test file::test name> | pass / fail / missing |

## Edge Cases
<One row per edge case in spec.md (EC-1, …). Test: the test that actually exercises this case, not just a test for an AC it cites. A spec without EC-IDs: split its Edge Cases bullets into single claims and number them here.>

| EC | Edge case | Test | Status |
|----|-----------|------|--------|
| EC-1 | <the claim> | <tests/file::test name, or —> | tested / untested / review only |

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
