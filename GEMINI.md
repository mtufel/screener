# Antigravity Development Instructions & Project Rules

## 1. Mandatory OpenSpec-Based Development
Every feature, architectural modification, or strategy enhancement MUST strictly follow the **OpenSpec** methodology before and during implementation:
- Create an OpenSpec change proposal under `openspec/changes/<change-name>/`.
- Include `proposal.md` (Problem statement, proposed solution, impact).
- Include `design.md` (Technical architecture, diagrams, state transitions, data flows).
- Include `specs/<capability>/spec.md` (Specific requirements and testable invariants).
- Include `tasks.md` (Checklist of discrete implementation tasks).
- Update and check off all tasks in `tasks.md` as implementation proceeds.
- Ensure 100% test coverage with automated unit/integration tests before finalizing.

## 2. Mandatory Git Branching Workflow
- All development work, features, bug fixes, and experiments MUST be created and implemented in a **new branch branched off `develop`** (e.g., `git checkout -b feat/<feature-name>` or `fix/<issue-name>`).
- Never make unisolated direct edits or commits directly to `main` / `master` / `develop` for new features.
- Always run the full test suite (`pytest -v`) on the feature branch to verify 100% pass rate before committing and pushing.

## 3. Existing Test Cases are Source of Truth (SOT)
- Existing test cases and scenario suites represent the **Source of Truth (SOT)** for system specifications, trading logic, and architectural invariants.
- **Strict Prohibition Against Modifying Existing Tests:** Existing test cases MUST NOT be modified, relaxed, deleted, or bypassed to make new code pass.
- **Bug Fix Exception Only:** Existing test cases may only be modified if there is a verified bug fix where the existing test case itself was asserting incorrect, invalid, or obsolete behavior.
- **Mandatory Justification Required:** Any change to an existing test case MUST include an explicit, documented justification in the commit message and PR/OpenSpec proposal explaining:
  1. Why the previous test assertion or expectation was buggy/incorrect.
  2. The exact root cause and why the updated assertion represents the true system invariant.
