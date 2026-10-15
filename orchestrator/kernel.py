"""
What it does: The orchestration kernel: a persisted state machine for one governed run. It decides
    which stages may start (dependency graph, entry/exit gates, recorded approvals), validates and
    verifies subagent results, handles retry -> fallback -> triage -> rollback, safe-stop/resume,
    re-planning (waive, spawn, amend, early cutoff), and writes the audit log, metrics and final
    report.

Why it is needed: This is the heart of the system. It never calls a model; it makes governance
    deterministic and testable, so it holds even if the orchestrating agent makes a mistake.
"""
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import verify
from .verify import validate
from . import workspace as wsp
from .audit import AuditLog, compute_metrics, read_log
from .gates import ENTRY_GATES, EXIT_GATES
from .graph import STATE_COLOURS, Artifact, GraphError, Stage, Workflow, digest_of
from .policy import Policy, PolicyViolation
from .workflow import load_workflow

ROOT = Path(__file__).resolve().parent.parent
PENDING, RUNNING, DONE, FAILED, ROLLED_BACK, STALE, SKIPPED, REJECTED, WAIVED, TRIAGE = (
    "PENDING", "RUNNING", "DONE", "FAILED", "ROLLED_BACK", "STALE", "SKIPPED", "REJECTED", "WAIVED", "AWAITING_TRIAGE")
SATISFIED = (DONE, WAIVED)
MAX_SUPERVISED_RETRIES = 1


class KernelError(Exception):
    """A refused transition (the caller asked for something governance does not allow)."""


def build_spec(requirement_file, name: str | None = None, config: dict | None = None) -> dict:
    """Describe a run from a requirement file plus an optional run config. The name defaults to the file name; the title to the first line.

    Example:
        build_spec("examples/inputs/url-shortener.md", config={"release_version": "1.0.0", "reviewer": {...}})
        -> {"name": "url-shortener", "title": "URL shortener service", "requirement": "<file text>",
            "release": {"version": "1.0.0"}, "approvals": {...}, "faults": {}, "amendment": None}
    """
    path = Path(requirement_file)
    text = path.read_text(encoding="utf-8")
    cfg = config or {}
    first = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()), path.stem)
    return {
        "name": name or re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-").lower() or "run",
        "title": cfg.get("title", first), "requirement": text, "release": {"version": cfg.get("release_version", "0.1.0")},
        "approvals": cfg.get("reviewer", {}), "faults": cfg.get("faults", {}), "amendment": cfg.get("amendment"),
    }


class Kernel:
    # ---- lifecycle ----------------------------------------------------------------------------------------

    def __init__(self, run_dir, root: Path = ROOT):
        """Load an existing run from <run_dir>/state.json (spec, project folder, workflow, per-stage state, artifacts, lineage) and reopen its
        audit log.

        Example:
            Kernel("runs/url-shortener") -> kernel positioned exactly where the run was saved
        """
        self.root, self.run_dir = Path(root), Path(run_dir).resolve()
        self.out = self.run_dir.parent
        doc = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        self.spec, self.workspace = doc["spec"], Path(doc["workspace"])
        self.policy = Policy(self.workspace, self.root / "policy.json")
        self.audit = AuditLog(self.run_dir / "audit.jsonl", run_id=self.spec["name"])
        self.wf = Workflow.from_dict(doc["workflow"])
        self.__dict__.update({k: doc[k] for k in ("state", "attempts", "tries", "agent_cur", "agent_used", "completed", "lineage", "faults", "guidance", "triaged", "status", "halt_reason", "scripted", "amended", "failure", "started")})
        self.last_error = doc.get("last_error", {})  # why a stage's previous attempt was rejected (shown to the agent on retry)
        self.mode = doc.get("mode") or "new-project"  # state files written before the mode was recorded
        self.artifacts = {k: Artifact(**v) for k, v in doc["artifacts"].items()}

    @classmethod
    def create(cls, requirement, out, root: Path = ROOT, project=None, config: dict | None = None, name: str | None = None, scripted: bool = False, faults: dict | None = None, resume: bool = False) -> "Kernel":
        """Start (or resume) a governed run for any requirement. New vs existing project is detected from the project folder. A fresh start
        resets the run folder and writes the initial state; `resume=True` reopens a halted run and puts stages that were mid-flight back to PENDING.

        Example:
            Kernel.create("examples/inputs/url-shortener.md", "runs", config=cfg, scripted=True)   (project defaults to runs/url-shortener/project)
            -> Kernel with every stage PENDING, mode "new-project", audit record 0 "run_started"
            Kernel.create(..., scripted=True) with a config that has no `reviewer` -> raises KernelError
        """
        out, root = Path(out).resolve(), Path(root)
        spec = build_spec(requirement, name, config)
        if faults:
            spec["faults"] = {**spec["faults"], **faults}
        if scripted and not spec["approvals"]:
            raise KernelError("--scripted needs a run config with a `reviewer` section (see examples/inputs/*.config.json)")
        run_dir = out / spec["name"]
        if resume:
            k = cls(run_dir, root)
            k.state = {sid: (PENDING if v == RUNNING else v) for sid, v in k.state.items()}
            k.status, k.halt_reason = RUNNING, None
            k.audit.append("run_resumed", done=[sid for sid, v in k.state.items() if v == DONE])
            k._activate()
            k.save()
            return k
        workspace = Path(project).resolve() if project else run_dir / "project"  # default: the project lives inside its run folder
        run_dir.mkdir(parents=True, exist_ok=True)
        for child in run_dir.iterdir():  # fresh start clears the run's files but never the project folder (it is the built software)
            if child.resolve() != workspace:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        workspace.mkdir(parents=True, exist_ok=True)
        for sub in ("results", "briefs", "artifacts"):
            (run_dir / sub).mkdir(parents=True)
        wf = load_workflow(root / "workflows" / "sdlc.json", root / ".claude" / "agents")
        doc = {
            "spec": spec, "workspace": str(workspace), "workflow": wf.to_dict(), "state": {sid: PENDING for sid in wf.stages}, "attempts": {}, "tries": {},
            "agent_cur": {st.id: st.agent for st in wf.stages.values()}, "agent_used": {}, "completed": [], "artifacts": {}, "lineage": [],
            "faults": spec["faults"], "guidance": {}, "triaged": {}, "status": RUNNING, "halt_reason": None,
            "scripted": scripted, "amended": False, "failure": None, "started": {}, "mode": wsp.project_mode(workspace),
        }
        (run_dir / "state.json").write_text(json.dumps(doc, indent=2))
        k = cls(run_dir, root)
        k.audit.append("run_started", stages=list(wf.stages), scripted=scripted, mode=k.mode)
        k._activate()
        k.save()
        return k

    @property
    def _pointer(self) -> Path:
        """Where the 'a run is active' marker lives: runs/ACTIVE for the default output folder (the hook reads it), or <out>/ACTIVE otherwise, so
        tests never touch the real one.

        Example:
            kernel with out == <repo>/runs -> Path("<repo>/runs/ACTIVE")
            kernel with out == /tmp/x      -> Path("/tmp/x/ACTIVE")
        """
        return (self.root / "runs" if self.out == (self.root / "runs").resolve() else self.out) / "ACTIVE"

    def _activate(self) -> None:
        """Write the active-run marker so the hook guard starts confining agents.

        Example:
            k._activate() -> runs/ACTIVE contains {"run_dir": ".../runs/demo", "workspace": ".../runs/demo/project"}
        """
        self._pointer.parent.mkdir(parents=True, exist_ok=True)
        self._pointer.write_text(json.dumps({"run_dir": str(self.run_dir), "workspace": str(self.workspace)}))

    def _deactivate(self) -> None:
        """Remove the active-run marker (the run finished or halted), so the guard stands down.

        Example:
            k._deactivate() -> runs/ACTIVE no longer exists
        """
        self._pointer.unlink(missing_ok=True)

    def save(self) -> None:
        """Persist the full run state atomically to <run_dir>/state.json (write temp file, then rename).

        Example:
            k.save() -> state.json updated; a crash mid-write cannot leave a partial file
        """
        doc = {k: getattr(self, k) for k in ("state", "attempts", "tries", "agent_cur", "agent_used", "completed", "lineage", "faults", "guidance", "triaged", "status", "halt_reason", "scripted", "amended", "failure", "started", "mode")}
        doc.update({"last_error": self.last_error, "spec": self.spec, "workspace": str(self.workspace), "workflow": self.wf.to_dict(), "artifacts": {k: vars(v) for k, v in self.artifacts.items()}})
        tmp = self.run_dir / "state.json.tmp"
        tmp.write_text(json.dumps(doc, indent=2, default=str))
        os.replace(tmp, self.run_dir / "state.json")
        self._write_workflow_doc()

    def _write_workflow_doc(self) -> None:
        """Write WORKFLOW.md into the run folder: the workflow diagram coloured by each stage's current state, and a table of every
        stage (agent, kind, dependencies, gates, approval, retries, fallback, state). Rewritten on every save, so it always shows
        stages added or waived at runtime.

        Example:
            k._write_workflow_doc() -> runs/<name>/WORKFLOW.md (returns None)
        """
        legend = " ".join(f"`{st}`" for st in STATE_COLOURS)
        rows = [
            f"| {s.id} | {self.agent_cur.get(s.id, s.agent)} | {s.kind} | {', '.join(s.deps) or '-'} | {s.entry_gate or '-'} | {s.exit_gate or '-'} | "
            f"{'yes' if s.high_impact else 'no'} | {'yes' if s.optional else 'no'} | {s.max_retries} | {s.fallback or '-'} | **{self.state[s.id]}** |"
            for s in self.wf.stages.values()
        ]
        doc = [
            f"# Workflow - {self.spec['title']}", "",
            f"Run: `{self.spec['name']}` | Project: **{self.mode}** | Status: **{self.status}**", "",
            "Each box is a stage; arrows mean 'must finish before'. A lock marks a high-impact stage that needs a recorded human approval. "
            "Independent stages (no arrow between them) run in parallel. Colour = current state.", "",
            "```mermaid", self.wf.to_mermaid(self.state), "```", "", f"States: {legend}", "",
            "| stage | agent | kind | depends on | entry gate | exit gate | approval | optional | retries | fallback | state |",
            "|---|---|---|---|---|---|---|---|---|---|---|", *rows, "",
            "Stages added or waived while the run was in progress (for example `clarify`, or a waived `impact`) appear here as they happen. "
            "Full history: `audit.jsonl`; final report: `FINAL_SUMMARY.md`.", "",
        ]
        (self.run_dir / "WORKFLOW.md").write_text("\n".join(doc), encoding="utf-8")

    # ---- context API used by gates / verify / report --------------------------------------------------------

    @property
    def workflow(self) -> Workflow:
        """The run's Workflow (property; named so gates can treat the kernel as a context).

        Example:
            k.workflow.stages["design"].deps -> ["requirements", "impact", "risk", "clarify"]
        """
        return self.wf

    def get(self, stage: str) -> dict:
        """Data of a finished stage's artifact. Raises KeyError if the stage has no artifact.

        Example:
            k.get("design")["stack"] -> {"language": "Python", "framework": "FastAPI", ...}
        """
        return self.artifacts[stage].data

    def has(self, stage: str) -> bool:
        """Does the stage have an artifact yet?

        Example:
            k.has("clarify") -> False (stage not run, or waived)
        """
        return stage in self.artifacts

    def decide(self, stage, decision, rationale, actor, inputs=None, output=None) -> None:
        """Add an entry to the decision lineage: who decided what, from which input digests, producing which output digest.

        Example:
            k.decide("implement", "approved", "Reviewed change set", "tech-lead (scripted)", inputs={"design": "ab12cd34ef56"})
            -> lineage gains {"n": 9, "stage": "implement", "decision": "approved", "actor": "tech-lead (scripted)", ...}
        """
        self.lineage.append({"n": len(self.lineage) + 1, "stage": stage, "decision": decision, "actor": actor, "rationale": rationale, "inputs": dict(inputs or {}), "output": output})

    def metrics(self) -> dict:
        """Reliability metrics for this run, computed from its audit log.

        Example:
            k.metrics() -> {"retries": 1, "rollbacks": 0, "approvals_granted": 2, "mttr_seconds": 0.77, "agent_runs": 8, ...}
        """
        return compute_metrics(read_log(self.audit.path))

    # ---- queries ------------------------------------------------------------------------------------------

    def _deps_ok(self, sid: str) -> bool:
        """Are all of a stage's dependencies finished (DONE) or waived?

        Example:
            k._deps_ok("design") -> False while `risk` is still PENDING; True once requirements, impact (waived), risk and clarify are satisfied
        """
        return all(self.state[d] in SATISFIED for d in self.wf.stages[sid].deps)

    def _dep_digests(self, sid: str) -> dict:
        """Digests of the artifacts a stage reads from its dependencies (waived stages have none). Recorded with the stage's own output so the
        kernel can tell later whether its inputs changed.

        Example:
            k._dep_digests("implement") -> {"requirements": "9c1e...", "design": "41ab..."}
        """
        return {d: self.artifacts[d].digest for d in self.wf.stages[sid].deps if d in self.artifacts}

    def _approved(self, sid: str) -> bool:
        """Is there a recorded human approval for this stage that matches its CURRENT inputs? An approval given before the inputs changed no
        longer counts.

        Example:
            approved earlier, design unchanged -> True
            approved earlier, design re-run with new content -> False (re-approval needed)
        """
        verdicts = [d for d in self.lineage if d["stage"] == sid and d["decision"] in ("approved", "rejected")]
        return bool(verdicts) and verdicts[-1]["decision"] == "approved" and {k: v for k, v in verdicts[-1]["inputs"].items()} == {k: v[:12] for k, v in self._dep_digests(sid).items()}

    def next(self) -> dict:
        """What may happen now. Returns the stages that are ready (dependencies satisfied, entry gate passed), which of those still need a human
        approval, stages running and stages awaiting triage. When nothing is left it finishes the run; if a scripted late change is configured
        it applies that first. A halted or failed run just reports its status. The orchestrator delegates every ready stage that needs no approval in one parallel batch.

        Example:
            k.next() -> {"status": "RUNNING", "ready": [{"stage": "impact", "agent": "impact-analyst", "kind": "agent",
                         "needs_approval": False, "description": "..."}, {"stage": "risk", ...}], "running": [], "awaiting_triage": []}
            (all done) -> {"status": "SUCCEEDED", "reason": None, "summary_file": ".../FINAL_SUMMARY.md"}
        """
        if (self.out / "STOP").exists() and self.status == RUNNING:
            self.stop("kill switch engaged", actor="operator")
        if self.status != RUNNING:
            return {"status": self.status, "reason": self.halt_reason, "failure": self.failure}
        self._invalidate_all()
        ready, running, triage = [], [s for s, v in self.state.items() if v == RUNNING], [s for s, v in self.state.items() if v == TRIAGE]
        for sid in self.wf.topo_order():
            if self.state[sid] not in (PENDING, STALE) or not self._deps_ok(sid):
                continue
            stage = self.wf.stages[sid]
            if stage.entry_gate and (reason := ENTRY_GATES[stage.entry_gate](self, stage)):
                self.audit.append("gate_blocked", sid, gate=stage.entry_gate, reason=reason)
                return self._fail_run(sid, f"entry gate '{stage.entry_gate}': {reason}")
            ready.append({"stage": sid, "agent": self.agent_cur[sid], "kind": stage.kind, "needs_approval": stage.high_impact and not self._approved(sid), "description": stage.description})
        if not ready and not running and not triage:
            if self.spec.get("amendment") and not self.amended and self.scripted:
                a = self.spec["amendment"]
                self.amend(a["stage"], a["patch"], a["actor"], a["reason"])
                return self.next()
            if any(v not in SATISFIED for v in self.state.values()):
                return self.stop("no runnable stages remain", actor="engine")
            return self._finish("SUCCEEDED")
        return {"status": "RUNNING", "ready": ready, "running": running, "awaiting_triage": triage}

    def status_report(self) -> dict:
        """Compact status for the CLI `status` command.

        Example:
            k.status_report() -> {"status": "RUNNING", "state": {"plan": "DONE", "requirements": "RUNNING", ...}, "attempts": {...}, "agent": {...}}
        """
        return {"status": self.status, "state": self.state, "attempts": self.attempts, "halt_reason": self.halt_reason, "agent": self.agent_cur}

    # ---- briefs ---------------------------------------------------------------------------------------------

    def _brief(self, sid: str, staging: Path | None) -> dict:
        """Write the task brief a subagent reads (requirement, project state, upstream artifact files, orchestrator guidance, project commands,
        where to work, exact output path) and return its paths.

        Example:
            k._brief("implement", Path("runs/demo/staging/implement"))
            -> {"brief": ".../briefs/implement.md", "result_path": ".../results/implement.json",
                "schema": ".../schemas/implement.json", "staging": ".../staging/implement"}
        """
        stage = self.wf.stages[sid]
        result = self.run_dir / "results" / f"{sid}.json"
        schema_path = self.root / "schemas" / f"{stage.id}.json"
        upstream = []
        for d in stage.deps:
            if d in self.artifacts:
                p = self.run_dir / "artifacts" / f"{d}.json"
                upstream.append(f"- `{p}` - {d}: {self.artifacts[d].summary[:200]}")
        if self.has("clarify") and "clarify" not in stage.deps and stage.id in ("design", "implement"):
            upstream.append(f"- `{self.run_dir / 'artifacts' / 'clarify.json'}` - human clarifications (a `change_request` overrides earlier decisions)")
        notes = [g["guidance"] for g in (self.get("plan")["stage_guidance"] if self.has("plan") else []) if g["stage"] == sid] + [self.guidance.get(sid, "")]
        lines = [f"# Task brief: {sid} (subagent: {self.agent_cur[sid]})", "", f"Stage: {stage.description}", f"Workspace state: **{self.mode}** (new-project = it started empty; existing-project = it started with code in `{self.workspace}`; this is fixed for the whole run - the code you may now see is what earlier stages produced)", "",
                 "## Requirement", "", self.spec["requirement"].strip(), "", "## Upstream artifacts (read the ones you need)", "", *(upstream or ["- none"]), ""]
        if stage.post == "plan":
            lines += ["## Workflow stages (you may only waive stages marked optional)", "", "```json", json.dumps([{"id": s.id, "agent": s.agent, "deps": s.deps, "optional": s.optional, "high_impact": s.high_impact, "description": s.description} for s in self.wf.stages.values()], indent=1), "```", ""]
        if sid in self.last_error:
            keep = " Your earlier work is still in the working folder named below; continue from it, do not start over." if staging else ""
            lines += ["## Your previous attempt was rejected - fix this", "", f"Reason: {self.last_error[sid]}", f"Re-write the result file exactly as the schema requires.{keep}", ""]
        if stage.post == "requirements":
            lines += ["## Workflow stage ids (use these in `tasks[].stage`; `clarify` is added if you report ambiguities)", "", ", ".join(self.wf.stages), ""]
        if any(notes):
            lines += ["## Orchestrator guidance for this stage", "", *(f"- {n}" for n in notes if n), ""]
        if staging:
            lines += ["## Where to work", "", f"Edit ONLY inside this isolated copy of the project: `{staging}`. Your changes are diffed, policy-checked and applied by the orchestrator; anything outside it is rejected.", ""]
        if self.has("design") and stage.id in ("implement", "tests"):
            c = self.get("design")["commands"]
            lines += ["## Project commands (from the design; use exactly these)", "", f"- setup: `{c['setup'] or '(none)'}`", f"- test: `{c['test']}`", f"- run: `{c['run']}`", ""]
        lines += ["## Terminology", "", "Say `new project` / `existing project` for the workspace state; do not use other labels for these scopes.", ""]
        lines += ["## Output", "", f"Write ONE JSON file to `{result}` that validates against the schema in `{schema_path}` (read it first). No other output file is read. Then reply with a single line: what you did.", ""]
        path = self.run_dir / "briefs" / f"{sid}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return {"brief": str(path), "result_path": str(result), "schema": str(schema_path), **({"staging": str(staging)} if staging else {})}

    def approval_input(self, sid: str) -> dict:
        """Collect the facts a human (and the approval-briefer subagent) needs to approve a high-impact stage, save them, and log the request.

        Example:
            k.approval_input("implement") -> {"approval_input": ".../briefs/approval-implement.json", "stage": "implement",
                                              "description": "HIGH IMPACT: ...", "upstream": {...}, "design": {"stack": {...}, "commands": {...}, ...}}
        """
        stage = self.wf.stages[sid]
        info = {"stage": sid, "description": stage.description, "upstream": {d: {"summary": self.artifacts[d].summary, "file": str(self.run_dir / "artifacts" / f"{d}.json")} for d in stage.deps if d in self.artifacts}}
        if self.has("design"):
            d = self.get("design")
            info["design"] = {"stack": d["stack"], "commands": d["commands"], "api": d["api"], "schema": d["schema"], "rollback_plan": d["rollback_plan"]}
        if self.has("implement"):
            info["files_changed"] = [f"{f['path']} ({f['status']})" for f in self.get("implement")["files"]]
        path = self.run_dir / "briefs" / f"approval-{sid}.json"
        path.write_text(json.dumps(info, indent=2))
        self.audit.append("approval_requested", sid)
        return {"approval_input": str(path), **info}

    def record_brief(self, sid: str, summary: str, recommendation: str) -> dict:
        """Log that an advisory brief was prepared for the approver (it never approves anything).

        Example:
            k.record_brief("implement", "Looks safe; check validators", "approve") -> {"recorded": True}
        """
        self.audit.append("brief_recorded", sid, "agent:approval-briefer", recommendation=recommendation, summary=summary[:300])
        return {"recorded": True}

    # ---- approvals ------------------------------------------------------------------------------------------

    def approve(self, sid: str, by: str = "", note: str = "", reject: bool = False, scripted: bool = False) -> dict:
        """Record a human decision for a high-impact stage, tied to the stage's current input digests. With `scripted=True` the decision comes from
        the run config's simulated reviewer and is labelled "(scripted)". A rejection halts the run safely. A real approval needs `by` (who decided).

        Example:
            k.approve("implement", by="Asha")           -> {"approved": True, "by": "human:Asha"}
            k.approve("implement", by="Asha", reject=True) -> {"status": "HALTED", "reason": "approval denied for 'implement' by human:Asha", ...}
            k.approve("implement")                      -> raises KernelError("--by <name> is required: record who made the decision")
        """
        stage = self.wf.stages[sid]
        if not stage.high_impact:
            raise KernelError(f"stage '{sid}' is not high-impact; no approval is needed")
        if scripted:
            if not self.scripted:
                raise KernelError("this run was not started with --scripted")
            entry = self.spec["approvals"].get(sid)
            approved, who, note = (bool(entry["approved"]), f"{entry['by']} (scripted)", entry.get("note", "")) if entry else (False, "scripted-reviewer", f"no scripted decision for {sid}")
        else:
            if not by.strip():
                raise KernelError("--by <name> is required: record who made the decision")
            approved, who = not reject, f"human:{by.strip()}"
        digests = {k: v[:12] for k, v in self._dep_digests(sid).items()}
        self.audit.append("approval_decision", sid, who, approved=approved, note=note)
        self.decide(sid, "approved" if approved else "rejected", note or "-", who, inputs=digests)
        if not approved:
            self.state[sid] = REJECTED
            return self.stop(f"approval denied for '{sid}' by {who}", actor=who)
        self.save()
        return {"approved": True, "by": who}

    # ---- stage execution ---------------------------------------------------------------------------------------

    def start(self, sid: str) -> dict:
        """Begin a stage. Refuses if it is not ready, an entry gate blocks, or a needed approval is missing. For an agent stage it writes the brief
        (and a staging copy for write-capable stages) and tells the orchestrator which subagent to delegate to. Human stages return their questions; kernel
        stages (release) run immediately; a configured fault drill fails the attempt before any agent is used.

        Example:
            k.start("risk") -> {"action": "delegate", "stage": "risk", "subagent": "risk-analyst", "brief": ".../briefs/risk.md",
                                 "result_path": ".../results/risk.json", "schema": ".../schemas/risk.json"}
            k.start("implement") (no approval yet) -> raises KernelError("stage 'implement' is high-impact: a recorded human approval ... is required first")
            k.start("clarify") -> {"action": "ask_human", "questions": [{"id": "A1", "question": "...", "default_assumption": "..."}]}
        """
        stage = self.wf.stages.get(sid) or (_ for _ in ()).throw(KernelError(f"unknown stage '{sid}'"))
        if self.status != RUNNING:
            raise KernelError(f"run is {self.status}")
        if self.state[sid] not in (PENDING, STALE) or not self._deps_ok(sid):
            raise KernelError(f"stage '{sid}' is not ready (state {self.state[sid]}, dependencies {[d for d in stage.deps if self.state[d] not in SATISFIED]} unfinished)")
        if stage.entry_gate and (reason := ENTRY_GATES[stage.entry_gate](self, stage)):
            self.audit.append("gate_blocked", sid, gate=stage.entry_gate, reason=reason)
            return self._fail_run(sid, f"entry gate '{stage.entry_gate}': {reason}")
        if stage.high_impact and not self._approved(sid):
            raise KernelError(f"stage '{sid}' is high-impact: a recorded human approval for the current inputs is required first")
        self.attempts[sid] = self.attempts.get(sid, 0) + 1
        self.tries[sid] = self.tries.get(sid, 0) + 1
        self.started[sid] = time.time()
        self.state[sid] = RUNNING
        self.audit.append("stage_started", sid, f"agent:{self.agent_cur[sid]}", attempt=self.tries[sid], total_attempts=self.attempts[sid])
        if stage.kind == "kernel":
            return self.complete(sid)
        if stage.kind == "human":
            qs = [{k: a[k] for k in ("id", "question", "terms", "default_assumption")} for a in self.get("requirements")["ambiguities"]]
            scripted = self.spec["approvals"].get(sid) if self.scripted else None
            self.save()
            return {"action": "ask_human", "stage": sid, "questions": qs, **({"scripted_answers": scripted} if scripted else {})}
        if self.faults.get(sid, 0) > 0:  # chaos drill: fails before any agent is delegated, so it costs nothing
            self.faults[sid] -= 1
            return {"injected_fault": True, **self._attempt_failed(sid, "injected fault")}
        staging = None
        if stage.writes:
            kept = self.run_dir / "staging" / sid
            if self.tries[sid] > 1 and kept.is_dir():  # a retry continues the earlier attempt's work instead of throwing it away
                staging = kept
            else:
                wsp.restore_pre_change(self.policy, self.run_dir, sid)  # re-run (re-plan): start again from the pre-change state
                staging = wsp.make_staging(self.workspace, self.run_dir, sid)
        out = {"action": "delegate", "stage": sid, "subagent": self.agent_cur[sid], **self._brief(sid, staging)}
        self.save()
        return out

    def complete(self, sid: str, result: str | None = None, usage: dict | None = None, answers: dict | None = None, note: str = "", by: str = "") -> dict:
        """Finish a stage from its result. Validates the result file against its schema, runs the stage's verification (apply the staged diff after policy
        checks / re-run the tests / merge the security scan / screen design commands / answers for human stages / the release checklist), then the exit gate.
        Success stores the artifact and invalidates stale dependents; failure is handled by retry, fallback or triage.

        Example:
            k.complete("risk", usage={"tokens": 18000, "tool_uses": 5})
            -> {"status": "DONE", "stage": "risk", "summary": "16 risks ...", "replanned": [], "invalidated": []}
            (result missing a required field) -> {"status": "RETRY", "stage": "risk", "error": "result does not match the schema: ...", "attempts_left": 1}
        """
        stage = self.wf.stages[sid]
        if self.state[sid] != RUNNING:
            raise KernelError(f"stage '{sid}' is not running (state {self.state[sid]})")
        self._record_run(sid, usage)
        try:
            if stage.kind == "human":
                if self.scripted and answers is None:
                    e = self.spec["approvals"][sid]
                    answers, note, by = e.get("answers", {}), e.get("note", ""), f"{e['by']} (scripted)"
                elif not by.strip():
                    raise KernelError("--by <name> is required: record who answered")
                else:
                    by = f"human:{by.strip()}"
                data = verify.clarify(self, answers or {}, note, by)
            elif stage.kind == "kernel":
                data = verify.release(self)
            else:
                data = self._read_result(sid, result)
                data = self._post(stage, data)
            if stage.exit_gate and (reason := EXIT_GATES[stage.exit_gate](self, stage, data)):
                raise _GateFailure(f"exit gate '{stage.exit_gate}': {reason}")
        except PolicyViolation as exc:
            self.audit.append("policy_violation", sid, f"agent:{self.agent_cur[sid]}", reason=str(exc))
            return self._attempt_failed(sid, f"policy violation: {exc}", policy=True)
        except (_GateFailure, _BadResult) as exc:
            return self._attempt_failed(sid, str(exc))
        return self._succeed(sid, data)

    def fail(self, sid: str, error: str, usage: dict | None = None) -> dict:
        """The orchestrator reports that a subagent crashed, timed out or refused. Counts as a failed attempt.

        Example:
            k.fail("design", "subagent timed out") -> {"status": "RETRY", "stage": "design", "agent": "solution-designer", "attempts_left": 1, ...}
        """
        if self.state[sid] != RUNNING:
            raise KernelError(f"stage '{sid}' is not running (state {self.state[sid]})")
        self._record_run(sid, usage)
        return self._attempt_failed(sid, error)

    def _record_run(self, sid: str, usage: dict | None) -> None:
        """Log that a subagent was delegated for this stage (always counted; token and tool-use figures only if the harness reported them).

        Example:
            k._record_run("risk", {"tokens": 18000, "tool_uses": 5}) -> audit "agent_run" with those figures
            k._record_run("risk", None)                               -> audit "agent_run" with empty details
        """
        if self.wf.stages[sid].kind == "agent":
            self.audit.append("agent_run", sid, f"agent:{self.agent_cur[sid]}", **{k: usage[k] for k in ("tokens", "tool_uses", "duration_ms") if usage and k in usage})

    def _read_result(self, sid: str, result: str | None) -> dict:
        """Load a subagent's result JSON and validate it against schemas/<stage>.json. Raises an internal retryable error if the file is missing,
        unreadable or does not match.

        Example:
            k._read_result("risk", None) -> {"summary": "...", "risks": [...]}   (valid)
            (file absent)                -> raises _BadResult("result file unreadable (risk.json): ...")
        """
        path = Path(result) if result else self.run_dir / "results" / f"{sid}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _BadResult(f"result file unreadable ({path.name}): {exc}") from None
        errors = validate(data, json.loads((self.root / "schemas" / f"{sid}.json").read_text(encoding="utf-8")))
        if errors:
            raise _BadResult("result does not match the schema: " + "; ".join(errors[:5]))
        return data

    def _post(self, stage: Stage, out: dict) -> dict:
        """Stage-specific processing of a validated result, done by the kernel instead of trusting the agent: `plan`/`requirements` add the run's project
        state; `design` screens its commands; `apply` diffs the staging copy, policy-checks and applies it; `tests` re-runs the tests; `security` merges a rule scan.

        Example:
            k._post(stage_tests, {"passed": 63, "failed": 0, "errors": 0, ...}) -> same data plus "returncode": 0, "verified_tail": [...]
            k._post(stage_implement, {...}) -> {"files": [...], "counts": {...}, "report": {...}, "summary": "19 added, 0 modified, ..."}
        """
        post = stage.post
        if post == "plan":
            return {**out, "mode": self.mode}
        if post == "requirements":
            return {**out, "mode": self.mode}
        if post == "design":
            for cmd in (out["commands"]["test"], out["commands"]["setup"] or "true", out["commands"]["run"] or "true"):
                self.policy.check_command(cmd)
            return {**out, "summary": f"{out['summary']} | stack: {out['stack']['language']}/{out['stack']['framework']} | test: {out['commands']['test']}"}
        if post == "apply":
            changes = wsp.collect_changes(self.run_dir / "staging" / stage.id)
            applied = wsp.apply_changes(self.policy, self.run_dir, stage.id, changes)
            c = applied["counts"]
            return {**applied, "report": out, "summary": f"{c['added']} added, {c['modified']} modified, {c['deleted']} deleted (+{applied['lines_added']}/-{applied['lines_removed']} lines); agent: {out['summary']}"}
        if post == "tests":
            return verify.run_tests(self, out)
        if post == "security":
            return verify.merge_security(self, out)
        return out

    def _succeed(self, sid: str, data: dict) -> dict:
        """Mark a stage DONE: store the artifact, log it, record the decision, apply any re-plan it requested, mark dependents whose inputs changed as
        STALE, and log an 'early cutoff' if a re-run produced identical output. A write-capable stage's staging copy is deleted here, because
        its diff has been applied and snapshotted.

        Example:
            k._succeed("risk", data) -> {"status": "DONE", "stage": "risk", "summary": "...", "replanned": [], "invalidated": []}
            requirements reporting ambiguities -> "replanned": ["spawn clarify", "design waits for clarify"]
        """
        stage = self.wf.stages[sid]
        had = sid in self.artifacts
        art = self._put(sid, data)
        self.state[sid] = DONE
        self.agent_used[sid] = self.agent_cur[sid]
        if sid in self.completed:
            self.completed.remove(sid)
        self.completed.append(sid)
        self.tries[sid] = 0
        self.last_error.pop(sid, None)
        if stage.writes:  # its diff is applied and snapshotted; the working copy is now redundant (kept after failures, for debugging)
            shutil.rmtree(self.run_dir / "staging" / sid, ignore_errors=True)
            try:
                (self.run_dir / "staging").rmdir()
            except OSError:
                pass
        self.audit.append("stage_succeeded", sid, f"agent:{self.agent_cur[sid]}", version=art.version, digest=art.digest[:12], duration=round(time.time() - self.started.get(sid, time.time()), 4), attempts=self.attempts.get(sid, 1))
        self.decide(sid, "completed", art.summary, f"agent:{self.agent_cur[sid]}", inputs=art.inputs, output=art.digest)
        replanned = self._replan(stage, data)
        invalidated = self._invalidate_dependents(sid)
        kids = self.wf.dependents(sid)
        if had and kids and not invalidated and all(self.state[c] == DONE for c in kids):
            self.audit.append("early_cutoff", sid, reason="output unchanged; downstream results remain valid")
        self.save()
        return {"status": "DONE", "stage": sid, "summary": art.summary, "replanned": replanned, "invalidated": invalidated}

    def _put(self, sid: str, data: dict) -> Artifact:
        """Store a new version of a stage's artifact (version+1, content digest, the input digests it derives from) and write it to artifacts/<stage>.json.

        Example:
            k._put("risk", {"summary": "...", "risks": [...]}) -> Artifact(stage="risk", version=1, digest="9c1e...", ...)
            (called again with changed data) -> version 2 with a different digest
        """
        prev = self.artifacts.get(sid)
        art = Artifact(sid, (prev.version + 1) if prev else 1, digest_of(data), data, self._dep_digests(sid), str(data.get("summary", ""))[:600])
        self.artifacts[sid] = art
        (self.run_dir / "artifacts" / f"{sid}.json").write_text(json.dumps(data, indent=2))
        return art

    # ---- failure handling ---------------------------------------------------------------------------------------

    def _attempt_failed(self, sid: str, error: str, policy: bool = False) -> dict:
        """Handle one failed attempt, in order: retry while attempts remain, then switch to the fallback agent (one attempt), then triage, or roll
        the run back at once for policy violations or when triage is used up. Never retries a policy violation.

        Example:
            first failure, max_retries=1   -> {"status": "RETRY", "stage": "tests", "agent": "test-engineer", "error": "...", "attempts_left": 1}
            docs retries used up           -> {"status": "FALLBACK", "stage": "docs", "agent": "technical-writer-lite", ...}
            everything used up             -> {"status": "TRIAGE", "options": ["retry", "rollback", "escalate"], "triage_input": ".../triage-tests.json", ...}
            policy violation               -> {"status": "FAILED", "failed_stage": "implement", "rolled_back": ["implement"], ...}
        """
        stage, audit = self.wf.stages[sid], self.audit.append
        self.last_error[sid] = error[:1500]
        audit("stage_failed", sid, f"agent:{self.agent_cur[sid]}", error=error[:500], attempt=self.tries[sid], retryable=not policy)
        if not policy:
            if self.tries[sid] <= stage.max_retries:
                audit("stage_retry", sid, attempt=self.tries[sid], of=stage.max_retries + 1)
                self.state[sid] = PENDING
                self.save()
                return {"status": "RETRY", "stage": sid, "agent": self.agent_cur[sid], "error": error, "attempts_left": stage.max_retries + 1 - self.tries[sid]}
            if stage.fallback and self.agent_cur[sid] != stage.fallback:
                audit("stage_fallback", sid, primary=self.agent_cur[sid], fallback=stage.fallback)
                self.agent_cur[sid], self.tries[sid], self.state[sid] = stage.fallback, stage.max_retries, PENDING
                self.save()
                return {"status": "FALLBACK", "stage": sid, "agent": stage.fallback, "error": error}
        audit("stage_exhausted", sid, error=error[:500], attempts=self.attempts.get(sid, 0))
        if policy or self.triaged.get(sid, 0) >= MAX_SUPERVISED_RETRIES:  # a model may not negotiate with policy; triage is bounded
            return self._fail_run(sid, error)
        self.state[sid] = TRIAGE
        info = {"stage": sid, "description": stage.description, "agent": self.agent_cur[sid], "error": error[:3000], "attempts_so_far": self.attempts.get(sid, 0), "earlier_guidance": self.guidance.get(sid),
                "upstream": {d: self.artifacts[d].summary for d in stage.deps if d in self.artifacts}}
        path = self.run_dir / "briefs" / f"triage-{sid}.json"
        path.write_text(json.dumps(info, indent=2))
        self.save()
        return {"status": "TRIAGE", "stage": sid, "options": ["retry", "rollback", "escalate"], "error": error, "triage_input": str(path), "result_path": str(self.run_dir / "results" / f"triage-{sid}.json"), "hint": "delegate to failure-triager, then call `triage`"}

    def triage(self, sid: str, action: str, guidance: str = "", rationale: str = "", by: str = "failure-triager") -> dict:
        """Apply the failure-triager's advice (a stage must be awaiting triage). `retry` grants one more supervised round with the guidance added to
        the next brief; `escalate` halts safely and keeps the work; `rollback` undoes the run. Retry is limited to one round per stage.

        Example:
            k.triage("tests", "retry", guidance="Check the build output first", rationale="flaky setup")
            -> {"status": "RETRY", "stage": "tests", "agent": "test-engineer"}
            k.triage("tests", "rollback") -> {"status": "FAILED", "failed_stage": "tests", "rolled_back": ["docs", "implement"], ...}
            k.triage("tests", "delete")   -> raises KernelError("action must be retry | rollback | escalate")
        """
        if self.state.get(sid) != TRIAGE:
            raise KernelError(f"stage '{sid}' is not awaiting triage")
        if action not in ("retry", "rollback", "escalate"):
            raise KernelError("action must be retry | rollback | escalate")
        self.triaged[sid] = self.triaged.get(sid, 0) + 1
        self.audit.append("triage_decision", sid, f"agent:{by}", action=action, rationale=rationale[:300], guidance=guidance[:300])
        self.decide(sid, f"triage:{action}", rationale or "-", f"agent:{by}")
        if action == "retry":
            self.guidance[sid], self.tries[sid], self.state[sid] = guidance, 0, PENDING
            self.save()
            return {"status": "RETRY", "stage": sid, "agent": self.agent_cur[sid]}
        if action == "escalate":
            self.state[sid] = PENDING  # resumable once a human has acted
            return self.stop(f"triage escalated '{sid}' to a human: {rationale}", actor=f"agent:{by}")
        return self._fail_run(sid, f"triage chose rollback: {rationale}")

    def _fail_run(self, sid: str, error: str) -> dict:
        """End the run as FAILED: log it, roll back, mark unfinished stages SKIPPED, and write the final report.

        Example:
            k._fail_run("tests", "tests not green ...") -> {"status": "FAILED", "failed_stage": "tests", "error": "...", "rolled_back": ["docs", "implement"], ...}
        """
        self.state[sid] = FAILED
        self.failure = [sid, error]
        self.audit.append("run_failure", sid, error=error[:500])
        rolled = self._rollback(sid)
        for s, v in self.state.items():
            if v in (PENDING, STALE, RUNNING, TRIAGE):
                self.state[s] = SKIPPED
        return {**self._finish("FAILED"), "failed_stage": sid, "error": error, "rolled_back": rolled}

    def _rollback(self, failed: str) -> list[str]:
        """Undo write-capable stages in reverse completion order by restoring their snapshots (the failed stage first). Returns the stages rolled back.

        Example:
            k._rollback("tests") -> ["docs", "implement"]   (the project folder is byte-for-byte as before the run)
        """
        rolled = []
        for sid in [failed] + [s for s in reversed(self.completed) if s != failed]:
            if not self.wf.stages[sid].writes:
                continue
            if wsp.rollback_snapshot(self.policy, self.run_dir, sid):
                self.audit.append("stage_rolled_back", sid)
                rolled.append(sid)
                if sid != failed:
                    self.state[sid] = ROLLED_BACK
                self.artifacts.pop(sid, None)
        return rolled

    def stop(self, reason: str, actor: str = "operator") -> dict:
        """Safe-stop: nothing new starts, nothing is rolled back, state is saved, and the run can be resumed later.

        Example:
            k.stop("operator stop") -> {"status": "HALTED", "reason": "operator stop", "summary_file": ".../FINAL_SUMMARY.md"}
        """
        self.audit.append("safe_stop", actor=actor, reason=reason)
        self.status, self.halt_reason = "HALTED", reason
        return self._finish("HALTED")

    # ---- re-planning ----------------------------------------------------------------------------------------------

    def _replan(self, stage: Stage, data: dict) -> list[str]:
        """Apply the structural changes a finished stage asks for: requirements reporting ambiguities spawns the `clarify` human stage and makes
        `design` wait for it (cycle-checked); the planner may waive OPTIONAL stages only (anything else is rejected and logged).

        Example:
            k._replan(stage_plan, {"skip": ["impact"], "rationale": "no code yet"}) -> ["waive impact"]
            k._replan(stage_plan, {"skip": ["implement"], ...})                       -> [] and a "plan_rejected" audit record
        """
        done = []
        if stage.post == "requirements" and data.get("ambiguities") and "clarify" not in self.wf.stages:
            self.wf.add(Stage("clarify", "human", ["requirements"], "Human resolves requirement ambiguities", max_retries=0, kind="human"))
            self.state["clarify"], self.agent_cur["clarify"] = PENDING, "human"
            self.audit.append("replan", stage.id, action="spawn_stage", target="clarify")
            done.append("spawn clarify")
        if stage.post == "requirements" and "clarify" in self.wf.stages and "clarify" not in self.wf.stages["design"].deps and self.state["design"] == PENDING:
            try:
                self.wf.add_deps("design", ["clarify"])
                self.audit.append("replan", stage.id, action="add_dependency", target="design", deps=["clarify"])
                done.append("design waits for clarify")
            except GraphError as exc:
                self.audit.append("replan_rejected", stage.id, reason=str(exc))
        if stage.post == "plan":
            for target in data["skip"]:
                st = self.wf.stages.get(target)
                if st and st.optional and self.state[target] == PENDING:
                    self.state[target] = WAIVED
                    self.audit.append("plan_applied", stage.id, "agent:workflow-planner", action="waive_stage", target=target)
                    self.decide(target, "waived", data["rationale"], "agent:workflow-planner")
                    done.append(f"waive {target}")
                else:  # the planner may only waive optional stages that have not started
                    self.audit.append("plan_rejected", stage.id, "agent:workflow-planner", target=target, reason="not optional, unknown, or already started")
        return done

    def _invalidate_dependents(self, sid: str) -> list[str]:
        """After `sid` produced a new version, mark finished dependents STALE if the digest they were built from has changed.

        Example:
            design re-run with different content -> ["implement"] (implement becomes STALE)
            design re-run with identical content -> []            (early cutoff: downstream stays valid)
        """
        cur, hit = self.artifacts[sid].digest, []
        for child in self.wf.dependents(sid):
            art = self.artifacts.get(child)
            if self.state[child] == DONE and art and art.inputs.get(sid) != cur:
                self.state[child] = STALE
                self.audit.append("stage_stale", child, because=sid)
                hit.append(child)
        return hit

    def _invalidate_all(self) -> None:
        """On every `next`, re-check all finished stages against their recorded input digests and mark any whose inputs changed as STALE.

        Example:
            after k.amend("clarify", ...) -> design is STALE the next time `next` is called
        """
        for sid, stage in self.wf.stages.items():
            if self.state[sid] == DONE and any((d not in self.artifacts and self.state[d] != WAIVED) or (d in self.artifacts and self.artifacts[d].digest != self.artifacts[sid].inputs.get(d)) for d in stage.deps):
                self.state[sid] = STALE
                self.audit.append("stage_stale", sid, because="inputs changed since last run")

    def amend(self, sid: str, patch: dict, actor: str, reason: str) -> dict:
        """Apply a late change to a finished stage's artifact (a new version). Only dependents whose inputs really changed become stale and re-run;
        high-impact stages that re-run need approval again.

        Example:
            k.amend("clarify", {"change_request": "Rate limit must be 30/min"}, "ops-lead", "capacity review")
            -> {"amended": "clarify", "stale": ["design"]}
        """
        art = self.artifacts[sid]
        data = json.loads(json.dumps(art.data))
        for key, value in patch.items():
            data[key] = {**data[key], **value} if isinstance(value, dict) and isinstance(data.get(key), dict) else value
        new = self._put(sid, data)
        self.audit.append("amended", sid, actor, reason=reason, old=art.digest[:12], new=new.digest[:12], patch=patch)
        self.decide(sid, "amended", reason, actor, inputs=art.inputs, output=new.digest)
        self.amended = True
        stale = self._invalidate_dependents(sid)
        self.save()
        return {"amended": sid, "stale": stale}

    # ---- termination ------------------------------------------------------------------------------------------------

    def _finish(self, status: str) -> dict:
        """Close the run with a final status (SUCCEEDED, FAILED or HALTED): audit it, save state, write metrics.json and FINAL_SUMMARY.md, and clear
        the active-run marker.

        Example:
            k._finish("SUCCEEDED") -> {"status": "SUCCEEDED", "reason": None, "summary_file": ".../FINAL_SUMMARY.md"}
        """
        from .report import write_summary

        self.status = status
        self.audit.append("run_finished", status=status, halt_reason=self.halt_reason, failed_stage=self.failure[0] if self.failure else None)
        self.save()
        (self.run_dir / "metrics.json").write_text(json.dumps(self.metrics(), indent=2))
        write_summary(self, self.spec, self.run_dir)
        self._deactivate()
        return {"status": status, "reason": self.halt_reason, "summary_file": str(self.run_dir / "FINAL_SUMMARY.md")}


class _GateFailure(Exception):
    pass


class _BadResult(Exception):
    pass
