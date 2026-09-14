# Technical solution package template

Use only sections that materially support the current decision. Keep one technical specification by default; split out an ADR only when a decision deserves an independent history.

## Document control

- Status: Draft | In review | Approved | Superseded
- Revision date
- Approved product specification
- Current-system baseline

## 1. Design drivers

- Product requirements and non-functional requirements that shape the solution
- Binding constraints
- Facts, assumptions, and unknowns
- Explicit non-goals

## 2. Options and decisions

For each material choice, compare credible alternatives on the criteria that matter here. Record the decision, rationale, costs, and what evidence could cause reconsideration.

## 3. System context and architecture

- Users and external systems
- Main architecture and deployment shape
- Main data and control flows
- Trust, permission, and failure zones

## 4. Domain data and state

- Important entities and identifiers
- Ownership and source of truth
- State transitions, versions, retention, and deletion or rollback behavior
- Invariants and invalid states

## 5. Modules and interfaces

For each public module, state:

- responsibility and behavior hidden behind the interface;
- callers and dependencies;
- inputs, outputs, invariants, error modes, ordering, and performance expectations;
- why the seam exists and which adapters are real today;
- how callers and tests exercise the same interface.

Avoid a class catalog or directory-tree narrative.

## 6. Main and failure flows

Describe representative end-to-end flows and important failures. Include retry, idempotency, partial failure, degraded behavior, and recovery only where relevant.

## 7. Security, privacy, and compliance

Cover data classification, access control, secrets, external transmission, audit needs, and abuse or unsafe-output boundaries in proportion to risk.

## 8. Observability and operations

- Logs, traces, metrics, and correlation identifiers
- Health and failure signals
- Data or model version visibility
- Deployment, configuration, migration, rollback, backup, and recovery
- Cost and capacity assumptions

## 9. Verification and evaluation strategy

Map product outcomes to test seams and evidence. Separate deterministic tests, integration or end-to-end checks, project-specific quality evaluation, performance, security, and human review.

## 10. Delivery plan

Group work into a small number of milestones. For each medium-grained capability task include:

- task ID and outcome;
- requirements covered;
- dependencies and exclusions;
- affected module interfaces;
- acceptance evidence and test or evaluation method.

## 11. Requirement traceability

Map each Must requirement and important non-functional requirement to the relevant design section, delivery task, and verification evidence.

## 12. Risks and open technical decisions

Record impact, owner or next action, and the condition for closure. Do not hide unresolved design behind implementation language.

## 13. Approval

Record the approval role or decision and date without personal names unless explicitly required. Approval authorizes implementation against this design; it does not authorize unrelated scope expansion.
