# Kickoff prompt — first worker session

Use this to start the relay (activation checklist step 6): open a new Claude
Code cloud session on this repository, fill in the placeholders, and paste the
block below as the first message. Prerequisites: protocol files installed at
the repo root, work-plan issues created, integration branch pushed, fallback
routine armed.

---

> Start the autonomous relay on <OWNER/REPO> as the first WORKER session.
> Fetch and check out branch `<INTEGRATION_BRANCH>`, read CLAUDE.md (section
> "Autonomous session protocol"), CLAUDE_CODING_RULES.md and HANDOVER.md.
> Implement issue #<N> (the first issue of the work plan) on a feature branch
> following the "Worker session" protocol, open the PR targeting
> `<INTEGRATION_BRANCH>`, update HANDOVER.md, then spawn the reviewer session.
> Apply the 90% budget rule.
