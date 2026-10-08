# Git workflow

DatePipette uses short-lived branches, focused pull requests, and a linear, readable history. The repository should explain what changed and why without exposing temporary AI reasoning or requiring a reader to reconstruct the work from checkpoint commits.

## Branches

Create one branch for one coherent outcome from an up-to-date `main` branch.

| Prefix | Use |
| --- | --- |
| `feat/` | User-visible capability |
| `fix/` | Defect correction |
| `docs/` | Documentation-only change |
| `refactor/` | Internal restructuring without intended behavior change |
| `test/` | Test-only improvement |
| `chore/` | Tooling, CI, dependencies, or repository maintenance |

Use lowercase kebab-case after the prefix, for example `feat/dataset-preview` or `chore/phase-0-foundation`. Do not mix unrelated outcomes in one branch.

## Local execution plan

For non-trivial work, create or rewrite `.local/ACTIVE_PLAN.md` before implementation. The directory is excluded locally through `.git/info/exclude`, not the shared `.gitignore`.

The plan is living working memory for the developer and Codex. Update it as facts change, check off verified milestones, and record the exact next action. Do not stage or commit it. Before completing the branch, transfer durable outcomes to tracked documentation, ADRs, tests, and code comments where they belong.

## Commits

Use Conventional Commit subjects:

```text
<type>(optional-scope): <imperative summary>
```

Allowed types are `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`, `perf`, and `revert`.

Examples:

```text
feat(api): add readiness endpoint
fix(auth): reject expired service tokens
docs: define snapshot publication behavior
```

Each commit must represent one coherent, reviewable, verified change. Keep the subject concise, use the body for motivation and important trade-offs, and add `BREAKING CHANGE:` only when a public contract is intentionally incompatible. Do not commit generated noise, local plans, secrets, or unfinished checkpoints.

## Pull requests

Before opening a pull request:

1. Re-read the diff as a reviewer.
2. Run every relevant formatter, linter, type check, test, migration check, and contract check documented in `CONTRIBUTING.md`.
3. Update affected documentation in the same branch.
4. Confirm that no secret, customer data, snapshot, export, or local planning file is staged.
5. Complete the pull request template with evidence rather than unsupported claims.

Prefer squash merging so one focused pull request becomes one meaningful commit on `main`. Rebase the branch when necessary to resolve drift; do not create merge commits merely to synchronize with `main`. Delete the branch after merge.

## Codex working loop

For each task, Codex should:

1. Read the relevant source-of-truth documents and the complete active plan.
2. Inspect the repository before editing and preserve unrelated work.
3. Implement the smallest complete vertical slice.
4. Update the plan after each material discovery or milestone.
5. Validate in proportion to risk and inspect the final diff.
6. Update tracked documentation with durable knowledge.
7. Propose or create clean commits only after verification succeeds.

The active plan can be rewritten after completion for the next branch. Git remains the audit trail for accepted results; the local plan remains a tool for reaching them.
