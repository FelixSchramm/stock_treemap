# Start one Codex Cloud worker task

Use this prompt in a new Codex Cloud task with the prepared project environment. Replace the issue number and ID. Do not start a second task automatically.

> Work as the **worker** for GitHub issue #<NUMBER> (<ISSUE-ID>). Read the repository-root `AGENTS.md`, the complete issue, its linked decisions, and current branch/PR status before editing. Work on exactly this issue. Check that it remains open and has no active worker branch or PR; if that is unclear, stop and report it.
>
> Start from `agents/integration` and create a branch named `issue-<NUMBER>-<short-slug>`. Implement only the issue's scope. Keep real portfolio data, sheet links, credentials, and account identifiers out of all changes. Run the issue's acceptance checks and relevant project checks; report exact commands and results.
>
> Commit your changes and open a pull request targeting `agents/integration`. The PR must state the issue ID, what changed, checks and results, and unresolved questions. Add a concise handoff comment to the issue with the PR URL, full commit SHA, checks, and next action. Do not review, approve, merge, or automatically launch a reviewer. End the task after the handoff.
