---
name: retrospect-it
description: Analyse a closed ADLC evidence run and propose evidence-backed candidate lessons without changing policy, code, tests, CI, schemas, or skills.
---

# retrospect-it

Trust level: OBSERVE / PROPOSE ONLY.

Operate only on an ADLC run whose manifest records closure. Read its raw evidence, structured events, failures, interventions, verification, and derived traceability before proposing lessons.

Identify evidence-backed patterns involving workflow failures, repeated manual interventions, missing verification, unnecessary complexity, quality-gate regressions, specification problems, or evidence-system problems. Distinguish product-specific lessons from reusable ADLC lessons, cite supporting run artifacts for every proposal, and deduplicate substantially similar proposals.

Create proposals only through `.adlc/evidence.py lesson-create`. Never directly modify `AGENTS.md`, skills, CI, tests, schemas, or application code, and never promote a lesson. Unsupported suggestions are omitted. Human approval is required before any candidate lesson changes the ADLC.
