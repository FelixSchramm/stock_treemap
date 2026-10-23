# HANDOVER

Living handover document for the autonomous session chain
(see CLAUDE.md, section "Autonomous session protocol").
Update after every completed unit of work and before every handover.

**Last updated:** <YYYY-MM-DD> (setup session — no issue work started yet)
**Chain status:** not started

## Done

- (nothing yet — protocol and fallback routine set up on <YYYY-MM-DD>)

## In progress

- (nothing)

## Next step

- Start with issue **#<N> — <first issue title>**.
  Read the issue in full first.

## Open questions / decisions taken

- <Anything a fresh session must know that is not in an issue — e.g. data or
  credentials that are unavailable in cloud sessions. If a step cannot be
  verified in the cloud, implement and document it anyway and record here
  which steps still need a local run.>

## Known pitfalls

- Never force-push `<INTEGRATION_BRANCH>`.
- Issue PRs target the integration branch, not the default branch — GitHub's
  `Closes #N` auto-close does not fire there; the reviewer closes issues
  manually after the merge.
- Every issue PR is merged only by a reviewer session (see CLAUDE.md,
  "Reviewer session"); always record the open PR number and its state
  (review pending / findings open / merged) here.
- <Project-specific pitfalls, e.g. known-wrong metrics in the README until
  issue #N is done.>
