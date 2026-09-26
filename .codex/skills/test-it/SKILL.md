---
name: test-it
description: Independently verify an implementation against its approved specification and report evidence without repairing application code.
---

# test-it

Independently verify an implementation against its approved specification.

Trust level: OBSERVE / REPORT ONLY.

Rules:
- Read the approved specification and acceptance criteria, then inspect the implementation and existing tests.
- Map each acceptance criterion to deterministic verification and identify criteria that are inadequately tested or remain unverified.
- At this trust level, propose appropriate missing automated tests; add tests only when a human separately authorizes file changes.
- Run the repository-defined test and quality gates.
- When an ADLC evidence run is active, record traceability, test results, gaps, and runtime findings through the shared `.adlc` mechanism. This evidence write does not authorize product or test changes.
- Report passes, failures, gaps, and unverified criteria clearly.
- Do not change product requirements, repair production or application code, or merge changes.
- Remain independent from `implement-it`: report implementation failures rather than silently repairing them.
- When a real implementation gap is found, record it and stop for a human decision. An approved correction returns to `implement-it`, then `test-it` reruns in the same evidence run. Do not create or invoke a repair skill.

Select only relevant testing layers:
1. Unit tests for functions, components, and deterministic algorithms.
2. Integration or API tests for component interactions, endpoints, formats, and boundaries.
3. Runtime or startup smoke tests that start the real process and verify it is reachable and responds correctly.
4. End-to-end UI tests that exercise a user interface with representative data.

Do not force irrelevant layers onto a project. Where practical, runtime and end-to-end tests should avoid fragile assumptions such as fixed local ports.
