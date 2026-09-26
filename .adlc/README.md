# ADLC Runtime

Installed reusable ADLC runtime.

Current ADLC version: **V0.5**

This directory contains the deterministic infrastructure used to run and verify the project's AI-assisted Application Development Life Cycle.

It is intentionally separate from the application's own source code, dependencies, tests, and documentation.

---

## Purpose

The ADLC runtime coordinates and records the reusable development lifecycle:

```text
user request
    ↓
preflight
    ↓
evidence run
    ↓
specification
    ↓
human specification approval
    ↓
feature branch
    ↓
implementation
    ↓
independent testing
    ↓
remediation if required
    ↓
feature publication
    ↓
pull request
    ↓
CI
    ↓
human review
    ↓
human merge
    ↓
run closure
    ↓
retrospective
```

Where practical, predictable workflow behaviour is implemented deterministically rather than delegated to an AI agent.

---

# Directory contents

Typical installed structure:

```text
.adlc/
├── README.md
├── VERSION
├── install.json
├── config.json
├── adlc_config.py
├── evidence.py
├── gitops.py
├── lifecycle.py
├── preflight.py
├── quality-gates.json
├── pyproject.toml
├── uv.lock
├── schema/
├── tests/
└── evidence/
```

Not every generated directory exists before the first development run.

---

# Version

The installed ADLC version is defined by:

```text
.adlc/VERSION
```

For this installation:

```text
V0.5
```

The installation source and provenance are recorded in:

```text
.adlc/install.json
```

This typically records:

* source repository;
* Git release/tag;
* source commit;
* installation timestamp;
* installed ADLC version;
* hashes of managed files.

Do not manually edit `install.json` unless repairing a known installation problem.

---

# Configuration

Reusable project-level ADLC configuration is stored in:

```text
.adlc/config.json
```

Typical configuration:

```json
{
  "base_branch": "master",
  "repository_visibility": "public"
}
```

## `base_branch`

Defines the repository base branch used by the lifecycle and Git/GitHub automation.

## `repository_visibility`

Controls the default visibility used when the ADLC creates a GitHub repository.

Supported values:

```text
public
private
```

The current installation defaults to `public`.

---

# Preflight

Preflight inspects whether the project is ready for an ADLC run.

It checks items such as:

* ADLC version;
* repository identity;
* Git state;
* base/current branch;
* remote configuration;
* required Codex skills;
* CI presence;
* evidence subsystem;
* expected tooling;
* quality-gate integrity.

Preflight reports problems rather than silently rewriting project configuration.

Run it manually with:

```bash
python .adlc/preflight.py --base-branch master
```

Normal ADLC run creation may invoke the required bootstrap and preflight behaviour automatically.

---

# Git and GitHub automation

The ADLC contains deterministic Git/GitHub operations in:

```text
.adlc/gitops.py
```

Where required, the workflow can:

* initialise Git;
* verify Git identity;
* create or preserve the configured base branch;
* detect or create `origin`;
* create a GitHub repository;
* use configured repository visibility;
* push the base branch;
* push feature branches;
* establish upstream tracking;
* create pull requests.

Existing repositories and remotes must not be silently replaced.

Human review and merge remain human-controlled steps.

---

# Public publication safety

When repository visibility is `public`, the ADLC performs a narrow deterministic publication safety check before first publication.

It checks for obvious hazards such as:

* private keys;
* credentials;
* tokens;
* `.env` files;
* common secret-bearing filenames;
* secret-like assignment patterns;
* evidence inputs already considered unsafe by the evidence subsystem.

The scanner reports paths and hazard classes without intentionally printing secret values.

This is not a general confidentiality classifier.

It does not determine whether source code, documentation, or project information is commercially, legally, or personally sensitive.

The user remains responsible for deciding whether public publication is appropriate.

---

# Lifecycle

Lifecycle state is implemented in:

```text
.adlc/lifecycle.py
```

The state machine prevents invalid workflow progression where practical.

Typical progression:

```text
specification
→ human-specification-approval
→ feature-branch
→ implementation
→ independent-testing
→ pull-request
→ ci
→ human-review
→ merge
→ closed
→ retrospective
```

A remediation loop may occur after independent testing:

```text
independent-testing
→ remediation
→ human decision
→ implementation
→ independent-testing
```

The ADLC does not currently include a separate repair agent.

Approved remediation returns to `implement-it`.

---

# Codex skills

The runtime works with reusable Codex skills installed under:

```text
.codex/skills/
```

Expected skills are:

```text
spec-it
implement-it
test-it
retrospect-it
```

The skills provide AI-assisted judgement and generation.

The deterministic ADLC runtime provides state, evidence, Git operations, validation, and workflow guardrails.

---

# Evidence

Run evidence is stored under:

```text
.adlc/evidence/
```

This directory is normally excluded from Git.

Evidence exists to make development runs reconstructable and to support later ADLC improvement.

A run may record:

* original request;
* specification;
* specification approval;
* skill and constitution hashes;
* Git state;
* branch and commit information;
* implementation outcomes;
* verification results;
* CI outcomes;
* human decisions;
* failures and interventions;
* merge context;
* retrospective observations;
* candidate lessons.

Raw evidence and derived interpretation remain separate.

---

# Creating an evidence run

Normally the ADLC workflow creates the run when development begins.

A run may also be created manually:

```bash
python .adlc/evidence.py create \
  --request-file /path/to/sanitized-request.txt
```

Run creation uses the canonical ADLC version from:

```text
.adlc/VERSION
```

The caller should not need to manually choose the ADLC version.

---

# Evidence lifecycle

A typical evidence lifecycle is:

```text
create run
    ↓
record stage outcomes
    ↓
attach/reference evidence
    ↓
record decisions
    ↓
record verification and CI
    ↓
record Git/PR/merge context
    ↓
close run
    ↓
retrospective
    ↓
candidate lessons
```

Evidence should not claim workflow steps succeeded before the underlying filesystem, Git, GitHub, CI, or human action actually succeeded.

---

# Raw and derived evidence

Raw evidence is intended to preserve what happened.

Examples:

```text
request
specification
test output
CI references
Git information
human decisions
failure observations
```

Derived evidence may include:

```text
summaries
traceability
failure classifications
candidate lessons
```

Derived conclusions should reference supporting raw evidence where practical.

AI-generated interpretations do not automatically become policy.

---

# Candidate lessons

`retrospect-it` may create evidence-backed candidate lessons after a development run is complete.

A candidate lesson may propose changes to areas such as:

* deterministic ADLC controls;
* tests;
* skills;
* `AGENTS.md`;
* CI;
* documentation;
* evaluations;
* no action.

Candidate lessons are proposals only.

Human approval is required before a lesson is promoted into a future ADLC version.

---

# Verification

The installed ADLC has its own isolated Python verification environment.

This avoids depending on the application's own dependency environment.

Synchronise it with:

```bash
uv sync --project .adlc --frozen --extra dev
```

Run ADLC tests:

```bash
uv run --project .adlc --frozen pytest .adlc/tests
```

Run ADLC linting:

```bash
uv run --project .adlc --frozen ruff check .adlc
```

Run ADLC type checking:

```bash
uv run --project .adlc --frozen mypy \
  .adlc/evidence.py \
  .adlc/lifecycle.py \
  .adlc/preflight.py \
  .adlc/adlc_config.py \
  .adlc/gitops.py
```

The reusable quality-gate definition is stored in:

```text
.adlc/quality-gates.json
```

Existing deterministic quality gates must not be silently weakened.

---

# CI

The installed reusable ADLC workflow is:

```text
.github/workflows/adlc.yml
```

Its purpose is to verify the ADLC infrastructure itself.

The application's own CI remains separate.

For example:

```text
.github/workflows/adlc.yml
    → ADLC infrastructure

.github/workflows/ci.yml
    → application/product verification
```

The ADLC does not assume every consuming application uses Python.

---

# Starting development

Once the ADLC is installed, normal user interaction should be minimal.

A user should be able to provide a product request such as:

```text
Build a simple web application that ...
```

The repository's ADLC should then recognise the workflow and progress through the appropriate stages.

Users should not normally need to repeatedly instruct Codex to:

* start evidence capture;
* invoke `spec-it`;
* create a feature branch;
* invoke `test-it`;
* push the branch;
* create a pull request.

The workflow should carry these reusable responsibilities itself.

Human intervention should remain focused on genuine decisions.

---

# Human gates

Important human-controlled gates currently include:

* specification approval;
* remediation approval where required;
* final review;
* merge;
* candidate-lesson promotion.

The ADLC must not silently convert these procedural gates into automated actions.

---

# Failure handling

When a workflow step fails:

1. record the failure where appropriate;
2. avoid advancing lifecycle state falsely;
3. isolate the likely root cause;
4. avoid bundling unrelated corrective changes where practical;
5. rerun the affected deterministic verification;
6. continue only when the lifecycle requirements are satisfied.

A failed external GitHub operation must not leave PR, CI, or merge state recorded as successful.

---

# Managed files

Files installed by `adlc-bootstrap` are ADLC-managed infrastructure.

Installation provenance and hashes are stored in:

```text
.adlc/install.json
```

V0.5 does not currently implement automatic upgrades or managed-file merging.

Do not assume that manually modified ADLC files can later be upgraded automatically without conflict.

---

# Local evidence

The following is intentionally local by default:

```text
.adlc/evidence/
```

Do not commit run evidence automatically.

Evidence can contain:

* prompts;
* human feedback;
* debugging information;
* internal observations;
* failure details.

Even when it contains no passwords or tokens, it may still contain information inappropriate for public publication.

---

# Troubleshooting

## Unknown ADLC version

Check:

```bash
cat .adlc/VERSION
```

The runtime code and canonical version must agree.

For this release:

```text
V0.5
```

## Git identity missing

Check:

```bash
git config user.name
git config user.email
```

The ADLC does not invent a user identity.

## GitHub CLI unavailable

Check:

```bash
gh --version
```

## GitHub authentication unavailable

Check:

```bash
gh auth status
```

## ADLC tests failing

Run:

```bash
uv run --project .adlc --frozen pytest .adlc/tests -q
```

Then fix the ADLC infrastructure problem before continuing development.

## Quality gate reported as weakened

Compare:

```text
.adlc/quality-gates.json
```

against the base branch.

Changes that weaken deterministic verification require explicit human approval.

---

# What V0.5 does not do

V0.5 intentionally does not provide:

* automatic ADLC upgrades;
* automatic conflict merging;
* automatic human review;
* automatic merge;
* autonomous ADLC self-modification;
* automatic promotion of lessons;
* general-purpose workflow orchestration;
* database-backed evidence;
* dashboards;
* plugin dependency management.

These capabilities should only be introduced when real usage provides evidence that they are needed.

---

# Design principle

The ADLC follows a simple ratchet model:

```text
run software project
      ↓
collect evidence
      ↓
observe failure/friction
      ↓
propose improvement
      ↓
human review
      ↓
improve ADLC
      ↓
repeat
```

The objective is not maximum automation.

The objective is a development process that becomes progressively more:

* reliable;
* testable;
* measurable;
* reproducible;
* reusable;
* appropriately autonomous.
