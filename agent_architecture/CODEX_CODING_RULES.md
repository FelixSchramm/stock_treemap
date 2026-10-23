# Codex worker and reviewer rules

Use these rules from the repository-root `AGENTS.md` when the workflow is activated.

## Workers

- Treat one issue as the complete scope. Do not fold in nearby cleanup or redesign.
- Inspect the base branch, issue, relevant code, and tests before editing.
- Keep domain calculations independent from UI and external services where practical.
- Prefer the project's existing conventions and established libraries. Avoid speculative abstractions and configurable behavior without a concrete requirement.
- Use only synthetic portfolio fixtures. Do not retrieve or print real Sheet contents during ordinary code tasks.
- Write deterministic tests for financially meaningful calculations and import edge cases. Do not use live network responses as unit-test fixtures.
- Run focused tests plus the repository's required checks. Report failures and environment limits plainly.
- Keep the PR reviewable: one issue, small commits, clear description, exact test evidence.

## Reviewers

- Review the full PR against its target branch and the issue's acceptance criteria.
- Check data correctness, privacy boundaries, security checks, failure states, and whether existing behavior outside scope changed.
- Report only actionable findings. Include severity, concrete evidence, and the change needed. Separate a blocking defect from a preference.
- Do not modify the worker branch. A separate follow-up worker handles accepted findings, preserving an auditable author/reviewer handoff.
- State what you tested in the review and what you could not verify.

## Shared safety and ownership

- Never include sheet links/IDs, positions, costs, holdings, credentials, or unredacted private provider results in source, issues, PRs, logs, or fixtures.
- Keep auth before every real-data access path. Tests may use synthetic identities and records.
- Do not infer owner identity, OAuth credentials, product mappings, cost basis, hosting budget, or publication permission.
- Do not change repository visibility, publish a deployment, merge, or close issues unless the owner explicitly requests that action.
