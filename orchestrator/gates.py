"""
What it does: The named entry/exit gates a stage must pass (ambiguities resolved, approvals
    recorded, audit chain intact, design complete, files written, tests green, no high security
    findings, release go).

Why it is needed: Gates are the quality and compliance checkpoints between stages. They are code,
    not prompts, so an agent cannot talk its way past them.
"""
from .audit import verify_log


def ambiguities_resolved(ctx, stage):
    """Entry gate. Blocks a stage while the requirements agent reported ambiguities and the human clarification stage has not finished.

    Example:
        requirements artifact has 3 ambiguities, no `clarify` artifact -> "requirement ambiguities are unresolved (clarify stage has not completed)"
        no ambiguities, or `clarify` is done                           -> None  (gate passes)
    """
    req = ctx.get("requirements") if ctx.has("requirements") else {}
    if req.get("ambiguities") and not ctx.has("clarify"):
        return "requirement ambiguities are unresolved (clarify stage has not completed)"


def audit_chain_intact(ctx, stage):
    """Entry gate. Blocks a stage if the audit log fails its hash-chain check.

    Example:
        intact log   -> None
        edited log   -> "audit log integrity check failed: hash chain broken at record 3"
    """
    ok, msg = verify_log(ctx.audit.path)
    return None if ok else f"audit log integrity check failed: {msg}"


def approvals_recorded(ctx, stage):
    """Entry gate for release. Every high-impact stage upstream of this one must have an approval as its latest recorded verdict.

    Example:
        `implement` approved                  -> None
        `implement` never approved / rejected -> "no recorded human approval for high-impact stage 'implement'"
    """
    for sid in sorted(ctx.workflow.ancestors(stage.id)):
        if not ctx.workflow.stages[sid].high_impact:
            continue
        verdicts = [d["decision"] for d in ctx.lineage if d["stage"] == sid and d["decision"] in ("approved", "rejected")]
        if not verdicts or verdicts[-1] != "approved":
            return f"no recorded human approval for high-impact stage '{sid}'"


ENTRY_GATES = {
    "ambiguities_resolved": ambiguities_resolved,
    "audit_chain_intact": audit_chain_intact,
    "approvals_recorded": approvals_recorded,
}


def design_complete(ctx, stage, data):
    """Exit gate for design. The design must define an interface and a test command, and every risk id in the register must map to a
    validation.

    Example:
        risks R1..R3 in the register, risk_coverage covers R1,R2 -> "risks without a validation plan: ['R3']"
        empty `api` list                                         -> "design defines no externally visible interface"
        all present                                              -> None
    """
    if not data.get("api"):
        return "design defines no externally visible interface"
    if not data["commands"]["test"].strip():
        return "design defines no test command"
    risks = {r["id"] for r in ctx.get("risk")["risks"]}
    covered = {c["risk_id"] for c in data["risk_coverage"] if c["validation"].strip()}
    if risks - covered:
        return f"risks without a validation plan: {sorted(risks - covered)}"


def files_written(ctx, stage, data):
    """Exit gate for implement. The applied change set must contain at least one file.

    Example:
        {"files": []}                                  -> "implementation produced no files"
        {"files": [{"path": "app/main.py", ...}]}      -> None
    """
    if not data.get("files"):
        return "implementation produced no files"


def tests_green(ctx, stage, data):
    """Exit gate for tests. Uses the kernel's independent re-run: non-zero exit, zero passes, or any failure/error blocks it.

    Example:
        {"returncode": 0, "passed": 63, "failed": 0, "errors": 0} -> None
        {"returncode": 1, "passed": 60, "failed": 3, "errors": 0} -> "tests not green: 60 passed, 3 failed, 0 errors, independent exit code 1"
    """
    if data["returncode"] != 0 or data["passed"] == 0 or data["failed"] or data["errors"]:
        return f"tests not green: {data['passed']} passed, {data['failed']} failed, {data['errors']} errors, independent exit code {data['returncode']}"


def no_high_findings(ctx, stage, data):
    """Exit gate for security. Any high-severity finding blocks; medium and low do not.

    Example:
        {"high": 0, "medium": 2} -> None
        {"high": 1}              -> "1 high-severity security finding(s)"
    """
    if data["high"]:
        return f"{data['high']} high-severity security finding(s)"


def release_go(ctx, stage, data):
    """Exit gate for release. Blocks unless the evidence-based decision is GO, and names the failed required checks.

    Example:
        {"decision": "GO", ...}                                         -> None
        {"decision": "NO-GO", "checklist": [{"name": "tests green...", "ok": False, "required": True}]}
        -> "release readiness is NO-GO: tests green (independently re-run)"
    """
    if data["decision"] != "GO":
        return "release readiness is NO-GO: " + "; ".join(c["name"] for c in data["checklist"] if not c["ok"] and c["required"])


EXIT_GATES = {
    "design_complete": design_complete,
    "files_written": files_written,
    "tests_green": tests_green,
    "no_high_findings": no_high_findings,
    "release_go": release_go,
}
