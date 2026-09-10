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
- All development work, features, bug fixes, and experiments MUST be created and implemented in a **new branch branched off `main` / `master`** (e.g., `git checkout -b feat/<feature-name>` or `fix/<issue-name>`).
- Never make unisolated direct edits or commits directly to `main` / `master` for new features.
- Always run the full test suite (`pytest -v`) on the feature branch to verify 100% pass rate before committing and pushing.
