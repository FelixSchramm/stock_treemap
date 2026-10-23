# Start one Codex Cloud reviewer task

Use this in a **separate, fresh** Codex Cloud task after a worker has opened a PR. Supply the PR URL and linked issue number. The reviewer does not inherit the worker task's uncommitted files or conversation.

> Work as the **reviewer** for <FULL PR URL>, linked to GitHub issue #<NUMBER>. Read the repository-root `AGENTS.md`, the full issue, the PR description and comments, and the complete PR diff against `agents/integration`. Review the exact PR head commit: <FULL SHA>.
>
> Check correctness against the issue's acceptance criteria, tests, data/privacy boundaries, and unintended changes. Run relevant checks in this task where possible; distinguish newly run checks from results reported by the worker. Report findings with severity and file/line evidence, or explicitly say that you found none.
>
> Add the review as a PR comment. Do not edit the worker's branch, approve the PR, merge it, close the issue, or start another cloud task. If changes are required, end with “Next action: owner starts a fresh worker task for these findings.” If the review passes, end with “Next action: owner reviews and merges this PR.” Include the reviewed commit SHA and exact checks performed.
