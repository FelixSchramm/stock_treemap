# Agent Architecture (dormant)

> **STATUS: DORMANT.** Nothing in this folder does anything by itself. No agent
> session, no scheduled routine, and no GitHub issue is created by merely having
> these files in the repository. This is a template kit; it becomes active only
> when the activation checklist below is completed deliberately.

## What this is

A battle-tested setup for letting an autonomous relay of Claude Code cloud
sessions work through a repository improvement plan, one GitHub issue at a
time. It was developed and fully executed on
[`credit-risk-modelling`](https://github.com/FelixSchramm/credit-risk-modelling),
where a chain of single-purpose sessions implemented and reviewed 12 issues
without human intervention.

The core ideas:

- **One session, one job.** A *worker* session implements exactly one issue on
  a feature branch and opens a PR; a *reviewer* session reviews that PR with
  fresh eyes, fixes only clear-cut findings, merges, and spawns the next
  worker. No session does two jobs.
- **An integration branch** collects all reviewed merges. `main` is only
  touched at the very end, by a single PR that the human reviews.
- **`HANDOVER.md` is the only memory.** Git history plus this file are the only
  channel between sessions; snapshots go to `docs/handovers/`.
- **A fallback routine** (twice daily) restarts the relay if it stalls, and
  stands down silently while the relay is healthy or finished.
- **A budget rule** makes every session hand over cleanly before it runs out of
  context.

## Files in this folder

| File | Purpose |
|---|---|
| `CLAUDE.template.md` | The relay protocol. Fill the placeholders and copy to the repo root as `CLAUDE.md`. |
| `CLAUDE_CODING_RULES.md` | Coding standards the relay enforces. Copy to the repo root as-is (adjust to taste). |
| `HANDOVER.template.md` | Initial handover state. Fill placeholders, copy to the repo root as `HANDOVER.md`. |
| `handovers_README.md` | Copy to `docs/handovers/README.md` (the snapshot archive). |
| `prompts/kickoff_worker.md` | Prompt that starts the first worker session. |
| `prompts/fallback_routine.md` | Prompt + settings for the twice-daily fallback routine. |
| `prompts/review_phase.md` | *Optional:* prompt for a review agent that generates the work-plan issues in the first place. |

## Activation checklist

Work through these steps in order. Until step 6, nothing runs on its own.

1. **Create the work plan as GitHub issues.** Either write the issues yourself,
   or run the optional `prompts/review_phase.md` agent, which reviews the repo
   and creates prioritized, self-contained issues (P0-P3 labels). Each issue
   must be implementable without any context beyond its own text.
2. **Decide the parameters** and fill every `<PLACEHOLDER>` in
   `CLAUDE.template.md` and `HANDOVER.template.md`:
   `<OWNER/REPO>`, `<INTEGRATION_BRANCH>` (e.g. `agents/integration`),
   `<ISSUE_RANGE>` (e.g. `#2-#13`), `<ISSUE_ORDER>`, and the short project
   description.
3. **Install the files at the repo root** — this is what arms the protocol for
   Claude Code sessions (a `CLAUDE.md` is only auto-loaded from the repo root,
   never from a subfolder):
   - `CLAUDE.template.md` -> `/CLAUDE.md`
   - `CLAUDE_CODING_RULES.md` -> `/CLAUDE_CODING_RULES.md`
   - `HANDOVER.template.md` -> `/HANDOVER.md`
   - `handovers_README.md` -> `/docs/handovers/README.md`
4. **Create the integration branch** off the default branch and push it:
   `git checkout -b <INTEGRATION_BRANCH> && git push -u origin <INTEGRATION_BRANCH>`.
5. **Create the fallback routine** following `prompts/fallback_routine.md`
   (cron `0 6,18 * * *`, fresh session per firing). This is the safety net, not
   the starter — its prompt stands down unless the relay has stalled.
6. **Start the relay:** open a new Claude Code cloud session on this repository
   and paste `prompts/kickoff_worker.md` (placeholders filled). From here on
   the relay runs itself.

To pause the relay: disable the fallback routine and let the current session
finish (or interrupt it). To retire the setup after completion: merge the final
integration PR, disable the routine, and optionally remove the protocol files
from the root again.
