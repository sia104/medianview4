---
name: spec-it
description: Convert a user request into a precise, testable specification without writing implementation code.
---

# spec-it

Use this skill to convert a user request into a precise, testable specification.

Rules:
- Do not write implementation code.
- Capture the minimally sufficient product behavior, constraints, acceptance criteria, and product-specific test expectations.
- Before making a product requirement mandatory, identify its provenance: it must be directly requested, necessary to remove ambiguity or make acceptance objectively testable, or an explicit human-approved design choice.
- Put reasonable additions without one of those provenances under Optional Recommendations instead of making them mandatory.
- Put optional improvements in a separate optional or recommendations section; do not make them mandatory.
- Describe what the product must do. Do not copy reusable ADLC governance, runner versions, CI tooling, merge controls, or generic quality gates into a product specification unless the human made them product requirements.
- Resolve choices that materially affect deterministic output or acceptance; do not leave alternatives when they can produce different accepted results.
- If user intent does not determine such a choice, ask the human where necessary or mark it unresolved and requiring approval.
- Do not let the implementer silently choose behavior that changes expected outputs. Give acceptance criteria an unambiguous expected outcome wherever practical.
- Save specifications under `specs/`.
- When an ADLC evidence run is active, contribute the request, specification, status, and approval references through the shared `.adlc` mechanism; do not write a separate large evidence report.
- Mark the specification as pending human approval until the human approves it.
- Do not proceed to implementation from this skill.
