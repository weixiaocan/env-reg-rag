---
name: design-solution
description: Turn an approved product specification into a reviewable technical solution and medium-grained delivery plan. Use for architecture, module interfaces, data and state design, technical tradeoffs, verification strategy, or implementation planning. Do not use to redefine product scope, implement code, or execute release testing.
---

# Design Solution

Design a technical solution that satisfies an approved product specification, makes consequential tradeoffs explicit, and leaves implementation tasks that can be verified independently.

## Working principles

- Require an approved product specification or an explicit user decision to proceed with unresolved product assumptions. Do not silently solve product ambiguity with architecture.
- Treat requirements and constraints as the source of design pressure. Do not begin with a preferred framework or copy another project's architecture.
- Explore credible alternatives for consequential decisions. Record an ADR only when the choice materially affects cost, risk, operability, data, or future change.
- Prefer deep modules: a small interface should hide substantial behavior. Introduce a seam where behavior genuinely varies or isolation creates clear leverage; one hypothetical adapter does not justify a public interface.
- Design failure behavior, observability, security, data lifecycle, deployment, rollback, and verification with the main flow rather than appending them after implementation.
- Define how important claims will be tested or evaluated before selecting optimizations.
- Keep the delivery plan at outcome level. Do not create one task per file, class, layer, or minor function.
- Keep active project documentation under one documentation root. Follow an existing convention; otherwise use `docs/`. Do not create competing architecture or decision documents in the repository root.
- Do not insert a person's real name into project artifacts unless explicitly required. Omit personal attribution fields or use role labels.

## Workflow

1. Read the approved product specification, relevant domain language, existing system evidence, and binding constraints. Identify conflicts or facts that still require verification.
2. Build a requirement-to-design trace for every Must requirement and important non-functional requirement.
3. Model the main data, state transitions, external actors, happy path, and important failure paths before choosing detailed technologies.
4. Propose the architecture and its module interfaces. State each interface's responsibilities, invariants, error modes, dependencies, and test seam without prematurely specifying every class or file.
5. Compare reasonable options for material choices. Capture accepted decisions and their costs; create ADRs only for decisions worth preserving independently.
6. Define data lifecycle, security and permission assumptions, observability, deployment shape, migration or compatibility needs, and rollback behavior in proportion to the project.
7. Define the verification strategy: which product outcomes are checked at which seam, which evidence must be collected, and what requires project-specific evaluation.
8. Split implementation into medium-grained, dependency-aware capability increments. Each task must deliver an observable result and include acceptance evidence.
9. Write or update the technical specification using [the solution package template](references/solution-package-template.md). Review it for requirement coverage, avoidable complexity, and unsupported claims.
10. Present material tradeoffs, assumptions, risks, and unresolved technical decisions. Stop before implementation until the user approves the solution.

## Inputs and outputs

Use the project's existing documentation root. If none exists, default to `docs/` and one `docs/TECH_SPEC.md` containing the architecture and delivery plan. Add separate files under `docs/adr/` only for consequential decisions that need an independent history. Inspect existing documents before writing; update the active artifact and identify superseded duplicates rather than leaving multiple documents that claim the same purpose.

Do not duplicate the full product specification. Link requirements by stable identifiers and explain how the design satisfies them.

## Task granularity

A good task delivers one demonstrable or measurable capability, may cross several modules, and can be completed and reviewed in one focused agent context. Split further only for a real dependency, risk seam, or context limit.

Each task records:

- intended outcome and covered requirements;
- inputs, outputs, and affected module interfaces;
- dependencies and important exclusions;
- observable acceptance evidence and relevant tests or evaluation.

Avoid task lists that mirror the directory tree. A milestone commonly contains a handful of capability tasks; this is guidance, not a quota.

## Completion gate

The solution is ready for implementation only when:

- every Must requirement has a credible design and verification path;
- main and failure flows, data ownership, and external dependencies are clear;
- public module interfaces are small enough for callers and tests to understand;
- material decisions include alternatives, rationale, and acknowledged costs;
- operational, security, observability, deployment, and rollback needs are addressed proportionally;
- implementation tasks produce verifiable outcomes without mechanical over-splitting;
- unresolved technical decisions are closed or explicitly accepted for a later task;
- the user or designated technical owner has approved the solution.

If the gate is not met, report what remains uncertain. Do not start coding to discover decisions that can be resolved safely at design time.
