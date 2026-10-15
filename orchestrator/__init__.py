"""
What it does: Marks `orchestrator/` as a Python package: the deterministic governance kernel for
    agentic SDLC runs.

Why it is needed: Python needs it to import the other modules. The kernel makes no model calls; the
    AI work is done by the subagents in .claude/agents/.
"""
