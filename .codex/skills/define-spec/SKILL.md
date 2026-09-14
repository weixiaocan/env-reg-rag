---
name: define-spec
description: Turn a product or feature idea into a reviewable, testable product specification before solution design begins. Use when defining requirements, writing or revising a PRD, clarifying scope, or establishing acceptance criteria. Do not use for architecture design, implementation planning, coding, or test execution.
---

# Define Spec

Create a product specification that explains what problem should be solved, for whom, within which boundaries, and how stakeholders will know it is successful.

## Working principles

- Treat available conversation, project documents, user research, and current-system evidence as inputs, not unquestioned truth.
- Keep business requirements separate from technical solutions. Record an unavoidable technical constraint, but defer architecture and library choices to solution design.
- Ask only questions whose answers would materially change scope, priority, risk, or acceptance. Otherwise state a visible assumption and continue.
- Prefer a small number of representative workflows over exhaustive speculative user stories.
- Use a few real examples to check that workflows and requirements reflect actual work. Do not build the complete test dataset, benchmark, or golden set during product specification; defer that work to evaluation design.
- Write acceptance criteria as observable outcomes. Do not claim that an implementation detail proves user-visible behavior.
- Make exclusions explicit. A clear out-of-scope section is part of the specification, not an optional appendix.
- Do not mark the specification approved on the user's behalf.
- Keep active project documentation under one documentation root. Follow an existing convention; otherwise use `docs/`. Do not scatter phase documents in the repository root.
- Do not insert a person's real name into project artifacts unless the user explicitly requires it. Omit owner/reviewer fields or use role labels such as `project maintainer`.

## Workflow

1. Inspect the current context and relevant existing artifacts. Identify known facts, assumptions, conflicts, and missing decisions.
2. Restate the problem from the user's or organization's perspective, without proposing architecture.
3. Identify users, stakeholders, primary workflows, constraints, risks, and success measures. When useful, ask for only enough real examples to verify the workflows are authentic.
4. Resolve consequential ambiguity with the user. Keep unresolved items in an open-decisions section with an owner or next action.
5. Draft or update the product specification using [the specification template](references/spec-template.md).
6. Check every in-scope requirement against at least one observable acceptance criterion. Remove duplicates and implementation leakage.
7. Present the material assumptions, exclusions, and unresolved decisions for review. Stop at `Draft` or `In review` until the user explicitly approves it.

## Inputs and outputs

Use the project's existing documentation root. If none exists, default to `docs/`, with the main output at `docs/PRODUCT_SPEC.md`. Before creating a new phase document, check whether an active document already owns that purpose; update it instead of creating a competing file.

The output must identify its status and revision date. When revising an existing specification, preserve still-valid decisions and summarize material changes rather than silently replacing them.

## Completion gate

The specification is ready for solution design only when:

- the problem, target users, and primary workflows are clear;
- in-scope and out-of-scope boundaries do not conflict;
- each must-have requirement has an observable acceptance criterion;
- success measures and important constraints are stated;
- unresolved decisions are either closed or explicitly accepted as later decisions;
- the user or designated owner has approved the specification.

If the gate is not met, report what remains undecided. Do not begin architecture or implementation merely to fill the gaps.
