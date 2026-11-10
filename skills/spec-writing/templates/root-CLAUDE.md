# <project name>

Built with specforge (spec-driven development with approval gates).

## Source of truth
- Requirements: `.spec/requirement.md`
- Spec and acceptance criteria: `.spec/spec.md`
- Task plan: `.spec/plan.md`
- Workflow state and approvals: `.spec/state.json` (never edit by hand)

## Constraints
- <The source's constraints and non-functional (E4) items, word for word. "None given." if there are none.>

## Rules
- Do not write code outside what the approved spec covers, and keep to the Constraints above.
- Every task follows TDD: red (failing test), green (minimum code), refactor.
- Every test traces to an AC-ID in `.spec/spec.md`.
- If the spec must change, stop and ask. A spec change resets approvals.
- Never merge a pull request.
