---
name: implement-it
description: Implement an approved specification without redefining requirements or independently changing acceptance criteria.
---

# implement-it

Use this skill to implement an approved specification.

Rules:
- Implement only from an approved specification in `specs/`.
- Confirm the active ADLC lifecycle has recorded specification approval and a feature branch distinct from the base/protected branch before editing.
- Do not redefine requirements or independently weaken acceptance criteria.
- If the approved specification is ambiguous, conflicting, or infeasible, stop and ask the human.
- Do not weaken, remove, bypass, or reduce an existing deterministic quality gate without explicit human approval recorded in the evidence run.
- For user-runnable software, create or update concise documentation covering setup or installation, launch, user access or use, and verification commands.
- Add or update deterministic tests for implemented behavior.
- When an ADLC evidence run is active, contribute specification references, changed-file or diff references, outcomes, and documentation changes through the shared `.adlc` mechanism; do not write a separate large evidence report.
- Run the repository-defined deterministic quality gates before completion.
- Perform implementation and normal implementation checks, not independent adversarial verification.
- In a remediation iteration, implement only the correction approved by the human after `test-it` reported the gap, and keep the iteration in the same run.
- Do not merge; humans control merge.
