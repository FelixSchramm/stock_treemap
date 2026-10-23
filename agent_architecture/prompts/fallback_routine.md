# Fallback routine — prompt and settings

The safety net for the relay (activation checklist step 5). It fires twice a
day, checks whether the relay is healthy, and only acts if it has stalled.
It never starts work on its own while the relay is active or finished.

## Settings

Create the routine from a Claude Code session on this repository (the
claude-code-remote MCP tools do this via `create_trigger`), or ask a session:
"Create a scheduled routine with the prompt below, cron `0 6,18 * * *`, and a
fresh session per firing."

- **Schedule:** `0 6,18 * * *` (06:00 and 18:00 UTC)
- **Mode:** fresh session per firing (`create_new_session_on_fire: true`)
- **Source:** this repository
- **Name suggestion:** `<Project> Chain Fallback (2x daily)`

## Prompt (fill the placeholders)

---

> You are the fallback for an autonomous work relay on the repository
> <OWNER/REPO>.
>
> 1. Fetch and check out the branch `<INTEGRATION_BRANCH>` and read
>    `CLAUDE.md` (section "Autonomous session protocol"),
>    `CLAUDE_CODING_RULES.md` and `HANDOVER.md`.
> 2. Check the state of GitHub issues <ISSUE_RANGE> on that repository.
> 3. Decide:
>    - If all issues <ISSUE_RANGE> are closed, or HANDOVER.md says the relay
>      is finished: do nothing and end your turn with a one-line note that the
>      relay is complete (suggest disabling this routine).
>    - If the latest commit on `<INTEGRATION_BRANCH>` (or an open issue
>      feature branch) is LESS than 12 hours old: the relay is active — do
>      nothing and end your turn silently.
>    - Otherwise the relay has stalled: follow the "Fallback sessions" rule in
>      CLAUDE.md — adopt the role matching the recorded state (open PR with
>      review pending → reviewer session; otherwise → worker session for the
>      next open issue), work that role's protocol, and spawn the successor
>      session as the protocol requires. Apply the 90% budget rule.
