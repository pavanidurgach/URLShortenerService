"""
What it does: Entry point for `python3 -m orchestrator <command>`; just forwards to cli.main().

Why it is needed: The orchestrator agent and the PreToolUse hook both invoke the kernel through this
    command line.
"""
import sys

from .cli import main

sys.exit(main())
