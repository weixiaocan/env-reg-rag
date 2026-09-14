---
name: build-review
description: Implement one approved, medium-grained capability and review it against its specification and acceptance evidence. Use after solution approval when developing or changing code. Do not use to redefine product scope, make unresolved architecture choices, or perform release-wide validation.
---

# Build and Review

Implement one observable capability at a time, prove it with focused evidence, and review the result before moving to the next capability.

## Entry gate

- Require an approved product specification and technical solution, plus one clearly identified capability task.
- Read the task's requirements, interfaces, exclusions, dependencies, and acceptance evidence before editing code.
- Inspect repository guidance, the current worktree, existing implementation and tests. Preserve unrelated user changes.
- If implementation would require changing product scope or a consequential architecture decision, stop and return to the appropriate design document rather than silently deciding in code.

## Workflow

1. Restate the capability as an observable outcome and map it to stable requirement IDs.
2. Turn its acceptance evidence into a small set of executable examples. For deterministic behavior, add or update a failing test first; for data, model or UI work, establish a fixed fixture and expected evaluation or visual evidence before implementation.
3. Implement the smallest coherent vertical slice that produces the outcome. Work across modules when needed; do not split work into one ticket per file, class or layer.
4. Run the narrowest meaningful tests during development, then all directly affected tests. Record actual commands and results; never infer a pass from code inspection.
5. Perform an implementation self-review against the approved specification, module contracts, failure behavior, observability, security boundaries and repository conventions.
6. Perform a separate review pass when the change is non-trivial. Prefer an independent reviewer or fresh context when available. Classify findings by impact, fix specification deviations and material defects, then rerun affected tests.
7. Update only the active project documents needed to reflect real behavior and evidence. Keep active documentation under the existing documentation root, avoid duplicate status documents, and do not add personal names or attribution fields unless explicitly required.

## Review boundaries

- Review the delivered capability, not the entire repository unless broader behavior was affected.
- Do not combine unrelated cleanup or speculative abstractions with the task.
- A framework adapter must not absorb domain rules that the approved design assigns to the application or domain layer.
- Generated output, model traces, screenshots and manual checks are evidence only when the input, configuration and result can be identified and reproduced.
- If a test fails because the specification and implementation disagree, resolve the disagreement explicitly; do not weaken the test merely to obtain green output.

## Completion gate

The capability is complete only when:

- its acceptance examples pass and important failure paths are covered;
- the implementation matches the approved requirements and module boundaries;
- focused test and evaluation commands have actual recorded results;
- review found no unresolved material defect or specification deviation;
- documentation describes only behavior that exists and avoids unsupported performance or production claims;
- remaining limitations and any user verification are stated clearly.

Stop after the selected capability is complete. Do not automatically start the next milestone or perform release approval.
