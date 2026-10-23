# Repository instructions for Codex

Copy this template to the repository root as `AGENTS.md` only when you are ready to activate the workflow. Instructions inside `agent_architecture/` are examples; Codex does not load them as repository-wide rules from that subfolder.

## Project

This repository contains a private portfolio dashboard. The current implementation plan is tracked in GitHub issues. Read the issue assigned in the current task and its acceptance criteria before changing code. Use synthetic fixtures only; never copy portfolio data, document links, account identifiers, credentials, or real provider responses into code, tests, logs, PRs, or handovers.

## Shared rules

- Do exactly one assigned role and one issue per task. Do not select another issue, start another task, or claim a handoff was executed.
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

Start in a separate Codex Cloud task with the exact PR URL. Inspect the full diff against `agents/integration`, verify the issue criteria and relevant tests, then record findings on the PR from most severe to least severe. Do not implement changes in the worker's branch. If findings remain, send the work back for a new worker task and stop. If the review passes, summarize the evidence and wait for the repository owner to merge. Do not approve or merge your own account's PR.

## Handoff

A reviewer handoff is complete only when the review status, blocking findings, required next action, and relevant PR commit SHA are written on the PR or issue. The next task must be started explicitly by the owner until an independently tested dispatcher is installed.
