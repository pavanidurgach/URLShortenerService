"""
What it does: Generates the Final Engineering Summary (FINAL_SUMMARY.md) from run state and the
    audit log: requirement, decomposition, design, orchestration trace, decision lineage, artifacts,
    risks, assumptions, metrics, limitations.

Why it is needed: Humans need one readable record of what was planned, decided, built and verified.
    It is generated, never hand-written, so it matches what actually happened.
"""
from .audit import read_log, verify_log

LIMITATIONS = [
    "Agents are Claude Code subagents: their output is non-deterministic. The orchestrator does not trust it - writes are staged and policy-checked, tests are re-run independently, the release decision is computed from evidence - but design quality still depends on the model.",
    "Subagents run project commands (setup/test). PreToolUse hooks confine file writes and screen commands, and the diff is policy-checked before apply, but this is not an OS sandbox; run in a container for untrusted requirements.",
    "The kernel cannot prove that a human gave an approval: it records who the orchestrator says decided (`human:<name>`) and refuses to start a high-impact stage without a recorded approval for the current inputs. Scripted approvals (`--scripted`) simulate reviewers for repeatable demos and are labelled '(scripted)'.",
    "Single-run JSON state; parallelism comes from the orchestrator delegating independent stages to subagents in one batch.",
    "Rollback covers workspace files (snapshot restore). There is no deployment target, so release readiness is a go/no-go decision, not a deployment.",
    "The audit log is tamper-evident (hash chain), not tamper-proof: anchoring the head hash externally is out of scope.",
]


def _table(headers, rows) -> str:
    """Render rows as a Markdown table (pipes and newlines escaped).

    Example:
        _table(["stage", "state"], [("plan", "DONE")])  -> "| stage | state |\\n|---|---|\\n| plan | DONE |"
    """
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers), *("| " + " | ".join(esc(c) for c in r) + " |" for r in rows)])


def write_summary(engine, spec: dict, run_dir) -> None:
    """Write FINAL_SUMMARY.md for a run from the kernel's state and audit log: requirement, ambiguities, task decomposition, impact,
    design, the orchestration table and Mermaid graph, decision lineage, control events, subagent runs, artifacts, release checklist,
    risks, assumptions, metrics and limitations.

    Example:
        write_summary(kernel, kernel.spec, kernel.run_dir)  -> writes runs/<name>/FINAL_SUMMARY.md (returns None)
    """
    ctx, wf, state = engine, engine.wf, engine.state
    a = ctx.artifacts
    metrics = engine.metrics()
    ok, msg = verify_log(ctx.audit.path)
    records = read_log(ctx.audit.path)
    out = [f"# Final Engineering Summary - {spec['title']}", "", f"**Run status:** {engine.status} | **Run:** {spec['name']} | **Release:** v{spec['release']['version']} | **Audit chain:** {'intact' if ok else 'BROKEN - ' + msg} ({len(records)} records) | **Subagent runs:** {metrics['agent_runs']} ({metrics['agent_tokens'] or 'tokens n/a'}, {metrics['agent_tool_uses'] or 'tool uses n/a'})", ""]

    out += ["## 1. Requirement and normalised problem", "", *("> " + l for l in spec["requirement"].strip().splitlines()), ""]
    if "requirements" in a:
        req = a["requirements"].data
        out += [f"Project: **{req['mode']}** | Kind of work: **{req['change_kind']}**. {req['problem']}", "", _table(["id", "type", "statement"], [(s["id"], s["type"], s["text"]) for s in req["statements"]]), ""]
        if req["ambiguities"]:
            out += ["### Ambiguities found and how they were resolved", "", _table(["id", "terms", "question", "default assumption"], [(x["id"], ", ".join(x["terms"]), x["question"], x["default_assumption"]) for x in req["ambiguities"]]), ""]
            if "clarify" in a:
                c = a["clarify"].data
                out += [f"Resolved by **{c['answered_by']}**:", "", _table(["ambiguity", "source", "decision"], [(k, v["source"], v["answer"]) for k, v in c["decisions"].items()]), ""]
                if c.get("guidance") or c.get("change_request"):
                    out += [f"Guidance: {c.get('guidance') or '-'}  |  Change request: {c.get('change_request') or '-'}", ""]

    out += ["## 2. Plan and rationale", ""]
    if "requirements" in a:
        out += ["### Task decomposition", "", _table(["task", "title", "workflow stage", "depends on"], [(t["id"], t["title"], t["stage"], ", ".join(t["depends_on"]) or "-") for t in a["requirements"].data["tasks"]]), ""]
    if "impact" in a:
        im = a["impact"].data
        out += ["### Codebase reasoning", "", f"Mode **{im['mode']}**. Impacted: `{im['impacted']}`; ripple: `{im['ripple']}`. {im['notes']}", ""]
        if im["data_flows"]:
            out += [_table(["entry point", "touches", "note"], [(f["entry"], ", ".join(f["touches"]), f["note"]) for f in im["data_flows"]]), ""]
    if "design" in a:
        d = a["design"].data
        st = d["stack"]
        out += [f"### Design (stack chosen by the design agent from the requirement: **{st['language']} / {st['framework']} / {st['build_tool']}** - {st['rationale']})", "", f"Test command: `{d['commands']['test']}` | Run: `{d['commands']['run']}`", "", _table(["id", "decision", "rationale"], [(x["id"], x["decision"], x["rationale"]) for x in d["adr"]]), "", "Interfaces: " + "; ".join(f"`{x}`" for x in d["api"]), ""]
        if d["schema"]:
            out += ["Schema changes: " + "; ".join(d["schema"]), ""]
        if d["decisions"]:
            out += [_table(["decision", "value", "source"], [(x["key"], x["value"], x["source"]) for x in d["decisions"]]), ""]

    out += ["## 3. Orchestration", "", "```mermaid", wf.to_mermaid(), "```", ""]
    lat = metrics["stage_latency_seconds"]
    out += [_table(["stage", "state", "attempts", "agent", "seconds", "artifact", "result"], [(s, state[s], engine.attempts.get(s, 0), engine.agent_used.get(s, "-"), lat.get(s, "-"), f"v{a[s].version} {a[s].digest[:8]}" if s in a else "-", a[s].summary[:140] if s in a else "-") for s in wf.topo_order()]), ""]
    out += ["### Decision lineage (who/what decided, from which inputs)", "", _table(["#", "stage", "decision", "actor", "rationale"], [(d["n"], d["stage"], d["decision"], d["actor"], d["rationale"]) for d in ctx.lineage]), ""]
    events = [r for r in records if r["event"] in ("replan", "replan_rejected", "amended", "stage_stale", "early_cutoff", "stage_retry", "stage_fallback", "stage_rolled_back", "policy_violation", "gate_blocked", "safe_stop", "triage_decision", "brief_recorded", "plan_applied", "plan_rejected", "approval_decision")]
    if events:
        out += ["### Control events (re-planning, recovery, guardrails)", "", _table(["seq", "event", "stage", "details"], [(r["seq"], r["event"], r["stage"] or "-", r["details"]) for r in events]), ""]
    runs = [r for r in records if r["event"] == "agent_run"]
    if runs:
        out += ["### Subagent runs", "", _table(["seq", "stage", "subagent", "tokens", "tool uses", "ms"], [(r["seq"], r["stage"], r["actor"].replace("agent:", ""), r["details"].get("tokens", "-"), r["details"].get("tool_uses", "-"), r["details"].get("duration_ms", "-")) for r in runs]), ""]

    out += ["## 4. Artifacts", ""]
    if "implement" in a:
        out += [_table(["file", "status", "+/-"], [(f["path"], f["status"], f"+{f['added']}/-{f['removed']}") for f in a["implement"].data["files"]]), ""]
    for key, label in (("tests", "Tests"), ("security", "Security review"), ("docs", "Documentation")):
        if key in a:
            out.append(f"- **{label}:** {a[key].summary}")
    if "release" in a:
        rel = a["release"].data
        out += ["", f"### Release readiness: **{rel['decision']}**", "", _table(["check", "ok", "detail"], [(c["name"], "yes" if c["ok"] else "NO", c["detail"]) for c in rel["checklist"]]), "", f"Full notes: `{rel['notes']}`"]
    out.append("")

    d = a["design"].data if "design" in a else {"risk_coverage": [], "tradeoffs": [], "assumptions": [], "limitations": []}
    out += ["## 5. Risks, trade-offs, validation", ""]
    if "risk" in a:
        cover = {c["risk_id"]: c["validation"] for c in d["risk_coverage"]}
        out += [_table(["id", "severity", "risk", "mitigation", "validation planned in design"], [(r["id"], r["severity"], r["title"], r["mitigation"], cover.get(r["id"], r["validation"])) for r in a["risk"].data["risks"]]), ""]
        if "tests" in a:
            out += [f"Evidence: {a['tests'].summary}", ""]
    out += ["### Trade-offs", "", *("- " + t for t in d["tradeoffs"]), ""]
    clar = a["clarify"].data["assumptions"] if "clarify" in a else []
    out += ["## 6. Assumptions", "", *("- " + t for t in clar + d["assumptions"]), ""]
    out += ["## 7. Reliability metrics (from the audit log)", "", _table(["metric", "value"], [(k, v) for k, v in metrics.items() if k != "stage_latency_seconds"]), ""]
    out += ["## 8. Limitations", "", *("- " + t for t in d["limitations"] + LIMITATIONS), ""]
    (run_dir / "FINAL_SUMMARY.md").write_text("\n".join(out), encoding="utf-8")
