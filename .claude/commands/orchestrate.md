---
description: Run a governed agentic SDLC workflow for any requirement file, delegating all work to subagents
argument-hint: <requirement-file> [--project DIR] [--config FILE] [--scripted] [--inject STAGE:N]
---

Act as the **orchestrator** defined in `.claude/agents/orchestrator.md`: read that file now and follow its procedure exactly for this run:

`python3 -m orchestrator init $ARGUMENTS`

The requirement can be anything the app should handle: a new project, a change to existing code (point `--project` at it), a bug fix, a tests-or-docs improvement, a precise requirement or an unclear one. Delegate all stage work to the subagents named by the kernel (Agent tool), run independent stages in parallel, and ask me (AskUserQuestion) for clarifications and approvals unless `--scripted` was given.
