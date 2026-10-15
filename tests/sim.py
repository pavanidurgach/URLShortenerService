"""
What it does: Test doubles: SimTeam plays the subagents (writes result files and staging edits for a
    tiny shell-script project) and drive() plays the orchestrator procedure against the kernel API.

Why it is needed: Lets the whole governance behaviour be tested quickly and for free, with no LLM.
    Using a shell project also shows the kernel is not tied to any stack.
"""
import json
from pathlib import Path

from orchestrator.kernel import Kernel, ROOT

INPUTS = ROOT / "examples" / "inputs"
TEST_SH = '#!/bin/sh\nset -e\nn=0\nfor t in tests/*.sh; do sh "$t"; n=$((n+1)); done\necho "$n passed"\n'


def start(out, name, scripted=True, faults=None, resume=False, project=None) -> Kernel:
    """Test helper: start a run for one of the example requirement files in examples/inputs/ using its run config.

    Example:
        start(tmp_path, "url-shortener") -> Kernel for that requirement, scripted reviewer on
    """
    cfg = json.loads((INPUTS / f"{name}.config.json").read_text())
    project = project or Path(out) / "project"  # tests that chain runs share one project folder on purpose
    return Kernel.create(INPUTS / f"{name}.md", out, ROOT, project, cfg, None, scripted, faults, resume)


class SimTeam:
    def __init__(self):
        """A fake team of subagents: records every call, keeps per-stage overrides for tests to inject behaviour, and the triage choice to return.

        Example:
            SimTeam().calls -> []
        """
        self.calls, self.batches, self.overrides, self.triage_action = [], [], {}, "rollback"

    def count(self, label):
        """How many times a simulated subagent ran.

        Example:
            team.count("tests") -> 2
        """
        return sum(c == label for c in self.calls)

    def perform(self, label, started):
        """Play one subagent: read its brief, make its staging edits, and write its result JSON to the path the kernel gave it.

        Example:
            team.perform("risk", {"brief": ".../briefs/risk.md", "result_path": ".../results/risk.json"}) -> results/risk.json now exists
        """
        self.calls.append(label)
        result, staging = Path(started["result_path"]), started.get("staging")
        brief = Path(started["brief"]).read_text() if started.get("brief") else ""
        fn = self.overrides.get(label) or getattr(self, f"on_{label}")
        out = fn(Path(staging) if staging else None, brief, result)
        if out is not None:
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_text(json.dumps(out))

    def artifact(self, result: Path, name):
        """Read another stage's artifact from the run folder, the way a real subagent reads upstream files.

        Example:
            team.artifact(Path(".../results/design.json"), "clarify") -> {"decisions": {...}, ...} or None
        """
        p = result.parent.parent / "artifacts" / f"{name}.json"
        return json.loads(p.read_text()) if p.exists() else None

    # ---- orchestrator advisers
    def on_plan(self, cwd, brief, result):
        """Simulated planner: waives `impact` for a new project (and tries an illegal waiver of `implement` for existing code, which the kernel must reject).

        Example:
            on_plan(None, "...**new-project**...", result) -> {"skip": ["impact"], "mode": "new-project", ...}
        """
        mode = "existing-project" if "**existing-project**" in brief else "new-project"
        return {"summary": f"{mode} plan", "mode": mode, "skip": ["impact"] if mode == "new-project" else ["implement"],  # existing project: an illegal waiver
                "stage_guidance": [{"stage": "implement", "guidance": "keep it minimal"}], "risks_to_watch": ["regression"], "rationale": "no code to analyse" if mode == "new-project" else "test"}

    def on_triage(self, cwd, brief, result):
        """Simulated failure-triager: returns whichever action the test configured.

        Example:
            team.triage_action = "escalate"; on_triage(...) -> {"action": "escalate", "guidance": "...", "rationale": "because", ...}
        """
        return {"summary": "t", "action": self.triage_action, "guidance": "check the build output first", "rationale": "because"}

    def on_brief(self, cwd, brief, result):
        """Simulated approval-briefer: always recommends approve_with_caution.

        Example:
            on_brief(...) -> {"recommendation": "approve_with_caution", "risks": ["r"], ...}
        """
        return {"summary": "b", "what_will_happen": "files change", "risks": ["r"], "checks_to_perform": ["read diff"], "recommendation": "approve_with_caution"}

    # ---- stage agents
    def on_requirements(self, cwd, brief, result):
        """Simulated requirements analyst: one statement per '- ' line; reports an ambiguity when the text mentions 'safer'.

        Example:
            requirement "- Make it safer." -> {"statements": [{"id": "S1", ...}], "ambiguities": [{"id": "A1", ...}], "change_kind": "new-build", ...}
        """
        req = brief.split("## Requirement")[1].split("## Upstream")[0]
        stmts = [{"id": f"S{i}", "text": t.strip("- ").strip(), "type": "functional"} for i, t in enumerate([l for l in req.splitlines() if l.startswith("- ")], 1)]
        ambs = [{"id": "A1", "terms": ["safer", "faster"], "statements": ["S1"], "question": "What does safer/faster mean?", "default_assumption": "block private hosts and add a cache"}] if "safer" in req else []
        return {"summary": f"{len(stmts)} statements", "problem": "p", "change_kind": "new-build", "statements": stmts, "ambiguities": ambs, "tasks": [{"id": f"T{s['id']}", "title": s["text"][:40], "stage": "implement", "depends_on": []} for s in stmts]}

    def on_impact(self, cwd, brief, result):
        """Simulated impact analyst.

        Example:
            on_impact(...) -> {"mode": "existing-project", "impacted": ["shorten.sh"], ...}
        """
        return {"summary": "impact", "mode": "existing-project", "impacted": ["shorten.sh"], "ripple": [], "data_flows": [], "notes": "n"}

    def on_risk(self, cwd, brief, result):
        """Simulated risk analyst with two risks (R1 high, R2 medium).

        Example:
            on_risk(...) -> {"risks": [{"id": "R1", "severity": "high", ...}, {"id": "R2", ...}]}
        """
        return {"summary": "2 risks", "risks": [{"id": "R1", "title": "regression", "severity": "high", "mitigation": "keep tests", "validation": "run all tests"}, {"id": "R2", "title": "abuse", "severity": "medium", "mitigation": "limit", "validation": "limit test"}]}

    def on_design(self, cwd, brief, result):
        """Simulated designer: a shell-script stack with test command `sh test.sh`; records a rate-limit decision when the clarify artifact has a change request.

        Example:
            on_design(...) -> {"stack": {"language": "sh", ...}, "commands": {"test": "sh test.sh", ...}, "decisions": [] or [{"key": "rate_limit", "value": "30/min"}]}
        """
        clar = self.artifact(result, "clarify") or {}
        change = bool(clar.get("change_request"))
        return {"summary": "shell design", "stack": {"language": "sh", "framework": "none", "build_tool": "none", "rationale": "tiny"},
                "commands": {"setup": "", "test": "sh test.sh", "run": "sh shorten.sh"}, "components": ["shorten.sh"], "api": ["shorten.sh create <url>"], "schema": [],
                "adr": [{"id": "ADR-1", "decision": "d", "rationale": "r"}], "decisions": [{"key": "rate_limit", "value": "30/min", "source": "change_request"}] if change else [],
                "assumptions": ["a"], "tradeoffs": ["t"], "limitations": ["l"], "rollback_plan": "revert files",
                "risk_coverage": [{"risk_id": "R1", "validation": "all tests"}, {"risk_id": "R2", "validation": "limit test"}]}

    def on_implement(self, cwd, brief, result):
        """Simulated implementer: writes a tiny shell project into its staging copy (core script first; later runs add expiry, then a config file).

        Example:
            first run  -> staging gets shorten.sh, test.sh, tests/test_core.sh, .gitignore (build/ output is ignored)
            later runs -> modifies shorten.sh / adds config.sh with RATE_LIMIT=30 or 60
        """
        design = self.artifact(result, "design")
        (cwd / "tests").mkdir(exist_ok=True)
        (cwd / "test.sh").write_text(TEST_SH)
        (cwd / ".gitignore").write_text("build/\n")
        (cwd / "build").mkdir(exist_ok=True)
        (cwd / "build" / "junk.o").write_text("ignored build output")
        if not (cwd / "shorten.sh").exists():
            (cwd / "shorten.sh").write_text('echo "short-$(echo "$2" | cksum | cut -d" " -f1)"\n')
            (cwd / "tests" / "test_core.sh").write_text('[ -n "$(sh shorten.sh create https://example.com)" ]\n')
        elif not (cwd / "tests" / "test_expiry.sh").exists():
            (cwd / "shorten.sh").write_text((cwd / "shorten.sh").read_text() + "# expiry added\n")
            (cwd / "tests" / "test_expiry.sh").write_text("grep -q expiry shorten.sh\n")
        else:
            (cwd / "config.sh").write_text(f"RATE_LIMIT={30 if any(d['value'] == '30/min' for d in design['decisions']) else 60}\n")
        return {"summary": "implemented", "test_command_used": "sh test.sh", "tests_passed": True, "iterations": 1, "notes": ""}

    def on_docs(self, cwd, brief, result):
        """Simulated technical writer: writes docs/README.md into its staging copy.

        Example:
            on_docs(staging, ...) -> {"summary": "docs", "files": ["docs/README.md"]}
        """
        (cwd / "docs").mkdir(exist_ok=True)
        (cwd / "docs" / "README.md").write_text("# docs\nrun: sh shorten.sh\n")
        return {"summary": "docs", "files": ["docs/README.md"]}

    on_docs_fallback = on_docs

    def on_tests(self, cwd, brief, result):
        """Simulated test engineer: claims 3 passed (the kernel's own re-run is what actually counts).

        Example:
            on_tests(...) -> {"passed": 3, "failed": 0, "errors": 0, "command": "sh test.sh", ...}
        """
        return {"summary": "ok", "passed": 3, "failed": 0, "errors": 0, "command": "sh test.sh", "failures": []}

    def on_security(self, cwd, brief, result):
        """Simulated security reviewer: reports no findings.

        Example:
            on_security(...) -> {"summary": "clean", "findings": []}
        """
        return {"summary": "clean", "findings": []}


def drive(k: Kernel, team: SimTeam, max_steps: int = 400):
    """Play the orchestrator agent against a kernel: loop on `next`, handle triage, approvals (scripted or by a named human), human stages, and
    delegate each ready batch to the simulated team, then `complete` each. Returns the final status dict (or the failure/halt dict).

    Example:
        drive(start(tmp_path, "url-shortener"), SimTeam()) -> {"status": "SUCCEEDED", "reason": None, "summary_file": ".../FINAL_SUMMARY.md"}
    """
    usage = {"tokens": 1000, "tool_uses": 5, "duration_ms": 10}
    for _ in range(max_steps):
        r = k.next()
        if r["status"] != "RUNNING":
            return r
        for sid in r["awaiting_triage"]:
            t = k.attempt_info[sid] if hasattr(k, "attempt_info") else None  # noqa: F841
            started = {"result_path": str(k.run_dir / "results" / f"triage-{sid}.json"), "brief": str(k.run_dir / "briefs" / f"triage-{sid}.json")}
            team.perform("triage", started)
            d = json.loads(Path(started["result_path"]).read_text())
            res = k.triage(sid, d["action"], d["guidance"], d["rationale"])
            if res["status"] in ("FAILED", "HALTED"):
                return res
        batch = []
        for item in r["ready"]:
            sid = item["stage"]
            if item["needs_approval"]:
                info = k.approval_input(sid)
                team.perform("brief", {"result_path": str(k.run_dir / "results" / f"brief-{sid}.json"), "brief": info["approval_input"]})
                k.record_brief(sid, "brief", "approve_with_caution")
                res = k.approve(sid, scripted=True) if k.scripted else k.approve(sid, by="tester")
                if res.get("status") in ("HALTED", "FAILED"):
                    return res
                item["needs_approval"] = False
            if item["kind"] == "kernel":
                res = k.start(sid)
                if res.get("status") in ("FAILED", "HALTED"):
                    return res
                continue
            started = k.start(sid)
            if started.get("status") in ("FAILED", "HALTED"):
                return started
            if started.get("action") == "ask_human":
                res = k.complete(sid) if k.scripted else k.complete(sid, answers={}, by="tester")
                if res.get("status") in ("FAILED", "HALTED"):
                    return res
            elif started.get("action") == "delegate":
                batch.append((sid, started))
        if batch:
            team.batches.append([s for s, _ in batch])  # delegated together = one parallel batch
            for sid, started in batch:
                team.perform(sid if started["subagent"] != "technical-writer-lite" else "docs_fallback", started)
            for sid, started in batch:
                res = k.complete(sid, usage=usage)
                if res.get("status") in ("FAILED", "HALTED"):
                    return res
    raise AssertionError("orchestration did not converge")
