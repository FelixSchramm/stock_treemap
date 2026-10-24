# Repository instructions for Codex

These instructions activate the repository workflow. The owner has authorized a coordinator in the current Cloud task to dispatch worker, reviewer, and follow-up subagents and process the open backlog. Each worker and reviewer handles one assigned issue or PR; the coordinator handles scheduling and handoffs. Subagents share the checkout, so run writers sequentially and reviewers read-only against a recorded commit SHA. This is not a dispatcher for independently running Cloud tasks after this task ends. This authorization does not supply missing credentials, financial decisions, hosting purchases, deployments, merges, or issue closure.

## Project

This repository contains a private portfolio dashboard. The current implementation plan is tracked in GitHub issues. Read the issue assigned in the current task and its acceptance criteria before changing code. Use synthetic fixtures only; never copy portfolio data, document links, account identifiers, credentials, or real provider responses into code, tests, logs, PRs, or handovers.

## Shared rules

- Workers and reviewers do exactly one assigned role and one issue per subagent. The coordinator may select ready issues and start subsequent subagents as authorized by the owner. Report only handoffs actually executed.
- Work from `agents/integration` and create one short-lived branch per issue or PR, as directed by the role prompt. Never force-push or push directly to `main`.
- Treat the GitHub issue and PR as the shared state between isolated Codex Cloud tasks. Do not rely on local files, memory from another task, or an uncommitted handover.
- Keep changes within the assigned issue. Prefer small, direct changes and established packages over speculative frameworks.
- Never commit secrets, personal data, real portfolio exports, local cache files, or credentials. Preserve the repository's ignore rules.
- Run relevant checks and report the exact commands and results. Never claim an unrun check passed.
- Ask for owner input in the issue when a required identity, credential, provider mapping, cost, or financial interpretation is missing. Do not guess it.
- Do not publish, deploy, change repository visibility, widen access, or merge a PR unless the task explicitly authorizes that action and the configured workflow permits it.

## Worker role

Implement one assigned open issue, satisfy its acceptance criteria, commit to the issue branch, and open a PR into `agents/integration`. Include the issue ID, change summary, checks run, and unresolved risks in the PR. Add a handoff comment on the issue with the PR URL and commit SHA. Stop there; do not perform the review or launch a reviewer task.

## Reviewer role

Start in a fresh reviewer subagent with the exact PR URL and commit SHA; use a fresh Codex Cloud task if the owner requests task-level filesystem isolation. Inspect the full diff against `agents/integration`, verify the issue criteria and relevant tests, then record findings on the PR from most severe to least severe. Do not implement changes in the worker's branch. If findings remain, send the work back for a new worker task and stop. If the review passes, summarize the evidence and wait for the repository owner to merge. Do not approve or merge your own account's PR.

## Handoff

A reviewer handoff is complete only when the review status, blocking findings, required next action, and relevant PR commit SHA are written on the PR or issue. The authorized coordinator starts the next subagent within the current Cloud task. Starting independent tasks after this task ends requires a separately tested dispatcher.
