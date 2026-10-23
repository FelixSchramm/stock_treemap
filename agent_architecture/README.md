# Codex Cloud worker and reviewer workflow

This folder contains **dormant templates** for using separate Codex Cloud tasks as a worker and a fresh reviewer. No task, agent chain, schedule, or branch is started by these files. Publishing a Codex Cloud environment prepares reusable dependencies and access; each task then gets its own isolated workspace. The GitHub issue and PR carry state between tasks. See [Codex Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments).

## Files

| File | Purpose |
| --- | --- |
| `AGENTS.template.md` | Copy to repository root as `AGENTS.md` only when activating repository instructions. |
| `CODEX_HANDOVER.template.md` | Checklist for the issue/PR comment shared between tasks. |
| `CODEX_CODING_RULES.md` | Focused review and implementation rules referenced from `AGENTS.md`. |
| `prompts/codex_worker.md` | Starts one issue implementation task and opens a PR. |
| `prompts/codex_reviewer.md` | Starts a new task to review an exact PR commit. |
| `prompts/codex_reviewer_followup.md` | Starts a worker task to address accepted review comments. |
| `prompts/codex_automation_design.md` | Scope for a future dispatcher; does not itself run or install one. |

## How handoff works

1. The owner starts a cloud task with the worker prompt and one ready issue.
2. The worker commits its issue branch, opens a PR into `agents/integration`, and writes the PR URL, commit SHA, and checks to the issue.
3. The owner starts a **separate fresh cloud task** with the reviewer prompt and exact PR URL/SHA.
4. The reviewer reports findings on the PR. If fixes are needed, the owner starts a new worker follow-up task. If review passes, the owner reviews and merges.
5. Only after the merge does the owner start the next issue's worker task.

Codex Cloud tasks do not share their working directories or automatically start one another. A committed PR and its issue discussion are the handoff channel. The first version of this workflow is therefore deliberately owner-started. A true unattended relay needs a separate dispatcher that checks PR and issue state, starts the next Cloud task, handles retries/idempotency, and records outcomes; the design prompt outlines that later work. Codex Cloud supports starting tasks from the CLI, but a prompt template by itself does not chain independent tasks. [Developer commands](https://learn.chatgpt.com/docs/developer-commands)

## Activation checklist

1. Reconcile `main` with `origin/main`; confirm the current repository visibility and decide whether its earlier history is acceptable for cloud use.
2. In Codex, create a Cloud environment with this GitHub repository, inspect its setup, then publish it. Start with no financial data or long-lived credentials. Repository instructions and synthetic tests should be enough for initial implementation tasks.
3. Review the existing [issue plan](../backlog/README.md). Begin with a ready issue such as M1-02 only after repository setup issues and dependencies are clear.
4. Create and push `agents/integration` from the agreed `main` base.
5. Copy `AGENTS.template.md` to the repository root as `AGENTS.md`, and `CODEX_CODING_RULES.md` to the root. Review the instructions as code before committing them.
6. Start one worker task manually. Inspect its PR and handoff, then start the reviewer task using the exact PR head SHA.
7. Refine the prompts only from observed behavior. Keep merge, issue closure, branch protection and sensitive access under owner control. Consider automatic dispatch only after the manual cycle is reliable.

Do not add Google Sheet access or portfolio secrets to a general-purpose shared environment. If a later task needs a credential, use the narrowest supported Cloud secret mechanism and authorized destination. Cloud environments support per-task workspaces and configurable variables/network secrets; they do not receive this Mac's files or sign-ins automatically. [Environment configuration](https://learn.chatgpt.com/docs/environments/cloud-environments)
