"""
What it does: Defines Artifact (a stage's versioned output with a content digest and the input
    digests it came from) and digest_of(); Stage (one workflow step with its dependencies, gates,
    retries, fallback); and Workflow (the dependency graph): cycle detection, ancestors/descendants,
    topological order, Mermaid export.

Why it is needed: The workflow is an explicit DAG, not a hard-coded sequence. The kernel needs this
    to find ready stages, run independent ones in parallel, join them, re-plan safely, and tell
    exactly which stages are stale after a change (Artifact digests).
"""
import hashlib
import json
from dataclasses import asdict, dataclass, field


# state -> (mermaid class name, fill colour), used to colour the workflow diagram
STATE_COLOURS = {
    "DONE": ("done", "#d4edda"), "WAIVED": ("waived", "#e2e3e5"), "RUNNING": ("running", "#cce5ff"), "PENDING": ("pending", "#ffffff"),
    "STALE": ("stale", "#fff3cd"), "FAILED": ("failed", "#f8d7da"), "ROLLED_BACK": ("rolledback", "#f5c6cb"),
    "SKIPPED": ("skipped", "#f0f0f0"), "REJECTED": ("rejected", "#f8d7da"), "AWAITING_TRIAGE": ("triage", "#ffe5b4"),
}


class GraphError(ValueError):
    pass


@dataclass
class Stage:
    id: str
    agent: str
    deps: list = field(default_factory=list)
    description: str = ""
    optional: bool = False  # the orchestrator agent may waive this stage; mandatory stages can never be waived
    high_impact: bool = False  # requires a recorded human approval before it may start
    max_retries: int = 2  # retries after the first attempt, on the primary agent
    fallback: str | None = None  # agent tried once after retries are exhausted
    entry_gate: str | None = None  # name in gates.ENTRY_GATES
    exit_gate: str | None = None  # name in gates.EXIT_GATES
    kind: str = "agent"  # agent = a Claude Code subagent; human = asks a person; kernel = computed from evidence
    writes: bool = False  # works in a staging copy whose diff is policy-checked and applied (rollback-capable)
    post: str | None = None  # kernel post-processing of the result: plan | requirements | design | apply | tests | security

    def to_dict(self) -> dict:
        """Plain-dict form of a stage, for saving into state.json.

        Example:
            Stage("docs", "technical-writer", ["implement"]).to_dict()
            -> {"id": "docs", "agent": "technical-writer", "deps": ["implement"], "max_retries": 2, "high_impact": False, ...}
        """
        return asdict(self)


class Workflow:
    def __init__(self, stages=()):
        """Build a workflow from an iterable of Stage objects (each added through `add`).

        Example:
            Workflow([Stage("a", "x"), Stage("b", "y", ["a"])]).stages.keys() -> ["a", "b"]
        """
        self.stages: dict[str, Stage] = {}
        for stage in stages:
            self.add(stage)

    def add(self, stage: Stage) -> None:
        """Add one stage. Duplicate ids are rejected.

        Example:
            wf.add(Stage("clarify", "human", ["requirements"]))   -> stage added
            wf.add(Stage("clarify", "human"))                      -> raises GraphError("duplicate stage clarify")
        """
        if stage.id in self.stages:
            raise GraphError(f"duplicate stage {stage.id}")
        self.stages[stage.id] = stage

    def validate(self) -> None:
        """Check the graph is sound: every dependency exists and there are no cycles.

        Example:
            Workflow([Stage("a","x",["zzz"])]).validate()                       -> raises GraphError("a depends on unknown stage zzz")
            Workflow([Stage("a","x",["b"]), Stage("b","y",["a"])]).validate()   -> raises GraphError("workflow contains a cycle")
        """
        for stage in self.stages.values():
            for dep in stage.deps:
                if dep not in self.stages:
                    raise GraphError(f"{stage.id} depends on unknown stage {dep}")
        self.topo_order()

    def topo_order(self) -> list[str]:
        """Stage ids in an order where every stage comes after its dependencies.

        Example:
            Workflow([Stage("a","x"), Stage("b","y",["a"]), Stage("c","z",["a"])]).topo_order() -> ["a", "b", "c"]
        """
        indegree = {sid: len(set(s.deps)) for sid, s in self.stages.items()}
        ready = [sid for sid, n in indegree.items() if n == 0]
        order = []
        while ready:
            sid = ready.pop(0)
            order.append(sid)
            for other in self.dependents(sid):
                indegree[other] -= 1
                if indegree[other] == 0:
                    ready.append(other)
        if len(order) != len(self.stages):
            raise GraphError("workflow contains a cycle")
        return order

    def dependents(self, sid: str) -> list[str]:
        """Stages that list `sid` as a direct dependency.

        Example:
            (implement is a dependency of tests, security, docs, release)
            wf.dependents("implement") -> ["tests", "security", "docs", "release"]
        """
        return [s.id for s in self.stages.values() if sid in s.deps]

    def descendants(self, sid: str) -> set[str]:
        """Every stage that depends on `sid`, directly or indirectly.

        Example:
            chain a -> b -> c : wf.descendants("a") -> {"b", "c"}
        """
        seen, todo = set(), [sid]
        while todo:
            for child in self.dependents(todo.pop()):
                if child not in seen:
                    seen.add(child)
                    todo.append(child)
        return seen

    def ancestors(self, sid: str) -> set[str]:
        """Every stage that `sid` depends on, directly or indirectly.

        Example:
            chain a -> b -> c : wf.ancestors("c") -> {"a", "b"}
        """
        seen, todo = set(), [sid]
        while todo:
            for dep in self.stages[todo.pop()].deps:
                if dep not in seen:
                    seen.add(dep)
                    todo.append(dep)
        return seen

    def add_deps(self, sid: str, deps: list[str]) -> None:
        """Add extra dependencies to a stage (used when the graph is re-planned at runtime). If the result would contain a cycle or an
        unknown stage, the change is reverted and an error is raised.

        Example:
            wf.add_deps("design", ["clarify"])  -> design now also waits for clarify
            wf.add_deps("a", ["c"])             -> (a -> b -> c exists) raises GraphError and leaves a's dependencies unchanged
        """
        stage = self.stages[sid]
        before = list(stage.deps)
        stage.deps = before + [d for d in deps if d not in before]
        try:
            self.validate()
        except GraphError:
            stage.deps = before
            raise

    def to_dict(self) -> list[dict]:
        """List-of-dicts form of the whole workflow, for saving.

        Example:
            wf.to_dict() -> [{"id": "plan", "agent": "workflow-planner", "deps": [], ...}, {"id": "requirements", ...}]
        """
        return [s.to_dict() for s in self.stages.values()]

    @classmethod
    def from_dict(cls, items) -> "Workflow":
        """Rebuild a Workflow from the list produced by `to_dict`.

        Example:
            Workflow.from_dict(wf.to_dict()).topo_order() == wf.topo_order()  -> True
        """
        return cls(Stage(**item) for item in items)

    def to_mermaid(self, states: dict | None = None) -> str:
        """Render the graph as Mermaid flowchart text (high-impact stages get a lock mark). With `states` (stage id -> state such as
        DONE, WAIVED, FAILED) each box is also coloured by its state.

        Example:
            wf.to_mermaid() -> 'flowchart LR\\n  plan["plan"]\\n  implement["implement 🔒"]\\n  plan --> requirements\\n  ...'
            wf.to_mermaid({"plan": "DONE"}) -> same text plus 'classDef done fill:#d4edda,...' and 'class plan done'
        """
        lines = ["flowchart LR"]
        for s in self.stages.values():
            label = f"{s.id}{' 🔒' if s.high_impact else ''}"
            lines.append(f'  {s.id}["{label}"]')
        for s in self.stages.values():
            lines.extend(f"  {dep} --> {s.id}" for dep in s.deps)
        if states:
            lines += [f"  classDef {css} fill:{fill},stroke:#555" for css, fill in STATE_COLOURS.values()]
            lines += [f"  class {sid} {STATE_COLOURS.get(st, STATE_COLOURS['PENDING'])[0]}" for sid, st in states.items() if sid in self.stages]
        return "\n".join(lines)


def digest_of(data) -> str:
    """Stable SHA-256 of any JSON-like data (keys sorted). Equal content gives an equal digest, which is how the kernel detects change.

    Example:
        digest_of({"a": 1, "b": 2}) == digest_of({"b": 2, "a": 1})  -> True
        digest_of({"a": 1}) == digest_of({"a": 2})                   -> False
    """
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Artifact:
    """A stage's versioned output. `inputs` maps each dependency to the digest this output was derived from, so a changed
    upstream digest marks exactly the affected stages stale (and an unchanged re-run output triggers early cutoff)."""

    stage: str
    version: int
    digest: str
    data: dict
    inputs: dict
    summary: str = ""
