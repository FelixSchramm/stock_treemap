# Optional future task: design a controlled Codex Cloud dispatcher

This is a design brief only. It must not create credentials, configure a schedule, install a GitHub Action, trigger Cloud tasks, merge PRs, or change repository settings.

> Inspect the repository and current official Codex Cloud developer documentation. Propose a minimal dispatcher for the existing worker/reviewer handoff. Compare a manually started Cloud-task workflow, CLI-driven orchestration, and a custom Agents API coordinator. Explain what each option can actually trigger and what service, credentials, hosting, cost, and maintenance it adds. Do not assume a prompt file can spawn another independent Cloud task.
>
> Define an explicit state machine for issue-ready → worker active → PR opened → reviewer active → changes requested or review passed → owner merge → next issue. The dispatcher must be idempotent, avoid duplicate runs, respect issue dependencies and branch protection, use bounded retries, preserve the PR/issue as audit trail, and stop for owner input when requirements or permissions are unclear.
>
> Keep financial source data out of the dispatcher. Specify least-privilege GitHub access, Cloud environment selection, secret handling, logging/redaction, cancellation, and human gates. Recommend the simplest safe version for this personal dashboard and list an incremental prototype that can be tested on a non-sensitive issue. Deliver a short design and acceptance criteria; do not implement the automation.
