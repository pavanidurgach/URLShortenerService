"""
What it does: Loads workflows/sdlc.json and validates it: every agent has a subagent file, gates and
    post-processors exist, there are no cycles, and write-capable stages use the policy-checked
    apply step.

Why it is needed: It keeps the process as reviewable data and rejects a broken workflow before any
    run starts.
"""
import json
from pathlib import Path

from .gates import ENTRY_GATES, EXIT_GATES
from .graph import GraphError, Stage, Workflow

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "workflows" / "sdlc.json"
POST_HOOKS = {"plan", "requirements", "design", "apply", "tests", "security"}


def load_workflow(path=DEFAULT_PATH, agents_dir=ROOT / ".claude" / "agents") -> Workflow:
    """Load workflows/sdlc.json into a Workflow and validate it: no cycles or unknown dependencies, every agent has a subagent file in
    .claude/agents/, gates and post-processors exist, and write-capable stages use the policy-checked `apply` step.

    Example:
        load_workflow().topo_order() -> ["plan", "requirements", "impact", "risk", "design", "implement", "tests", "security", "docs", "release"]
        (a stage names agent "nobody") -> raises GraphError("plan: no subagent file for ['nobody'] in .../.claude/agents")
    """
    wf = Workflow(Stage(**s) for s in json.loads(Path(path).read_text(encoding="utf-8"))["stages"])
    wf.validate()
    for s in wf.stages.values():
        if s.kind == "agent":
            missing = [n for n in [s.agent] + ([s.fallback] if s.fallback else []) if not (Path(agents_dir) / f"{n}.md").exists()]
            if missing:
                raise GraphError(f"{s.id}: no subagent file for {missing} in {agents_dir}")
        if (s.entry_gate and s.entry_gate not in ENTRY_GATES) or (s.exit_gate and s.exit_gate not in EXIT_GATES) or (s.post and s.post not in POST_HOOKS):
            raise GraphError(f"{s.id}: unknown gate or post-processor")
        if s.writes and s.post != "apply":
            raise GraphError(f"{s.id}: write-capable stages must use the 'apply' post-processor (policy-checked apply)")
    return wf
