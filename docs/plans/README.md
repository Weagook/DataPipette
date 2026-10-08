# Local execution plans

Use an execution plan for a cross-module feature, database migration, security-sensitive change, significant refactor, or multi-session implementation. The active plan lives at `.local/ACTIVE_PLAN.md` and is excluded from Git through `.git/info/exclude`.

This keeps temporary reasoning, abandoned approaches, and checkpoint notes out of the public history. It does not make product decisions private: every lasting decision and externally observable behavior must be moved into tracked documentation before the pull request is complete.

Each active plan must be self-contained and updated while work progresses. Include:

1. **Purpose and user-visible outcome**
2. **Current repository context**
3. **Scope and non-goals**
4. **Affected modules and contracts**
5. **Data model and migration**
6. **Security and failure behavior**
7. **Implementation milestones**
8. **Exact validation commands and expected observations**
9. **Progress log**
10. **Decision log**
11. **Retry, rollback, or recovery notes**

Keep exactly one active plan per branch. When the task is finished, transfer durable information to the appropriate tracked document and rewrite `ACTIVE_PLAN.md` for the next task. A plan is not a substitute for an ADR when the work introduces a durable architectural decision.
