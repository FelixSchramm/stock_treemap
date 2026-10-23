# Start a new worker task for review findings

Use this only after the owner has decided to address findings from a reviewer. Supply the PR URL, issue number, and review comment permalink.

> Continue the worker role for GitHub issue #<NUMBER> and PR <FULL PR URL>. Read the current repository-root `AGENTS.md`, the complete issue, the review comment <COMMENT URL>, and the PR's latest diff. Confirm the issue and branch are still open and that the PR head is the expected branch. Fix only the requested findings that fit the issue; report ambiguous or out-of-scope comments instead of guessing.
>
> Run relevant checks and report exact commands/results. Push the fix to the existing PR branch, update the PR description or issue with the new full commit SHA and a point-by-point response, and stop. Do not review, approve, merge, or launch another task.
