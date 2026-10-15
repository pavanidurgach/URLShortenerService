"""
What it does: Independent verification of agent claims: re-runs the test command itself, merges the
    security reviewer's findings with a pattern scan, builds the clarification record, and computes
    the release go/no-go checklist.

Why it is needed: An agent saying 'tests pass' or 'ready to release' is only a claim. These checks
    make the evidence come from the kernel, not from the model.

Also holds validate(): a small stdlib JSON-Schema validator (type, enum, required, properties,
    items, additionalProperties) used to check every subagent result against schemas/*.json.
"""
import subprocess

from .audit import verify_log
from .gates import approvals_recorded


def run_tests(k, out: dict) -> dict:
    """Independently re-run the project's tests. Runs the design's setup command, then its test command, in the project folder, and
    returns the agent's report plus the kernel's own exit code. The exit code is what the quality gate trusts.

    Example:
        run_tests(kernel, {"passed": 63, "failed": 0, "errors": 0, "summary": "ok", ...})
        -> {"passed": 63, ..., "returncode": 0, "verified_command": "python -m pytest -q", "verified_tail": [...],
            "summary": "63 passed, 0 failed, 0 errors; independent re-run exit=0 (verified)"}
        (agent says green but the real run fails) -> returncode 1 and summary "... DISAGREES with agent report"
    """
    cmds = k.get("design")["commands"]
    for c in (cmds["setup"], cmds["test"]):
        if c.strip():
            k.policy.check_command(c)
    for c in (cmds["setup"],):
        if c.strip():
            subprocess.run(c, shell=True, cwd=k.workspace, capture_output=True, text=True, timeout=1800)  # idempotent bootstrap
    proc = subprocess.run(cmds["test"], shell=True, cwd=k.workspace, capture_output=True, text=True, timeout=1800)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-10:]
    agree = (proc.returncode == 0) == (out["failed"] == 0 and out["errors"] == 0 and out["passed"] > 0)
    summary = f"{out['passed']} passed, {out['failed']} failed, {out['errors']} errors; independent re-run exit={proc.returncode} ({'verified' if agree else 'DISAGREES with agent report'})"
    return {**out, "returncode": proc.returncode, "verified_command": cmds["test"], "verified_tail": tail, "summary": summary}


def merge_security(k, out: dict) -> dict:
    """Combine the security reviewer's findings with a rule-based scan of every changed file, and count severities.

    Example:
        merge_security(kernel, {"summary": "ok", "findings": [{"file": "app/x.py", "line": 9, "rule": "ssrf", "severity": "medium", "detail": "..."}]})
        -> {"findings": [... agent finding + any pattern-scan hits ...], "files_scanned": 19, "high": 0, "medium": 1,
            "summary": "1 finding(s), 0 high; ok"}
    """
    findings = [{**f, "source": "agent"} for f in out["findings"]]
    scanned = 0
    for f in k.get("implement")["files"]:
        if f["status"] == "deleted":
            continue
        scanned += 1
        text = k.policy.resolve(f["path"]).read_text(encoding="utf-8")
        findings += [{**x, "detail": "policy pattern", "source": "pattern-scan"} for x in k.policy.scan(f["path"], text)]
    high = sum(f["severity"] == "high" for f in findings)
    summary = f"{len(findings)} finding(s), {high} high; {out['summary']}"
    return {"findings": findings[:60], "files_scanned": scanned, "high": high, "medium": sum(f["severity"] == "medium" for f in findings), "agent_summary": out["summary"], "summary": summary}


def clarify(k, answers: dict, note: str, by: str) -> dict:
    """Build the clarification record from the human's answers. Answered ambiguities use the answer; unanswered ones fall back to the
    requirements agent's default assumption, which is recorded as an assumption.

    Example:
        clarify(kernel, {"A1": "Aliases are case-sensitive"}, "keep it simple", "human:Asha")   (A1 and A2 are open)
        -> {"decisions": {"A1": {"answer": "Aliases are case-sensitive", "source": "human"},
                          "A2": {"answer": "<default>", "source": "default"}},
            "assumptions": ["A2: default assumed - <default>"], "guidance": "keep it simple", "answered_by": "human:Asha", ...}
    """
    ambs = k.get("requirements")["ambiguities"]
    decisions, assumptions = {}, []
    for a in ambs:
        answer = answers.get(a["id"])
        decisions[a["id"]] = {"answer": answer or a["default_assumption"], "source": "human" if answer else "default"}
        if not answer:
            assumptions.append(f"{a['id']}: default assumed - {a['default_assumption']}")
    data = {"decisions": decisions, "assumptions": assumptions, "guidance": note, "answered_by": by, "ignored_answers": sorted(set(answers) - {a["id"] for a in ambs})}
    data["summary"] = f"{len(decisions)} decisions, {len(assumptions)} assumed by default"
    return data


def release(k) -> dict:
    """Compute the go/no-go release decision from evidence and write release_notes.md. Checks: tests green (independently re-run), no
    high security findings, documentation present, every risk has a validation, audit chain intact, approvals recorded, change footprint
    within policy; open assumptions are reported but not required.

    Example:
        release(kernel) -> {"decision": "GO", "version": "1.0.0", "checklist": [{"name": "audit chain intact", "ok": True, "required": True, ...}, ...],
                            "notes": "release_notes.md", "summary": "GO v1.0.0: 7/8 checks"}
    """
    tests, sec, docs, risk, impl, design = (k.get(x) for x in ("tests", "security", "docs", "risk", "implement", "design"))
    chain_ok, chain_msg = verify_log(k.audit.path)
    approvals = approvals_recorded(k, k.wf.stages["release"])
    assumptions = design["assumptions"] + (k.get("clarify")["assumptions"] if k.has("clarify") else [])
    doc_files = []
    for f in docs["report"]["files"]:
        try:
            p = k.policy.resolve(f)
            if p.is_file() and p.stat().st_size > 0:
                doc_files.append(f)
        except Exception:  # noqa: BLE001 - an unresolvable path simply does not count as documentation
            pass
    check = lambda name, ok, detail, required=True: {"name": name, "ok": bool(ok), "required": required, "detail": detail}  # noqa: E731
    checklist = [
        check("tests green (independently re-run)", tests["returncode"] == 0 and tests["passed"] > 0 and tests["failed"] == 0 and tests["errors"] == 0, f"{tests['passed']} passed, exit {tests['returncode']}"),
        check("no high-severity security findings", sec["high"] == 0, f"{sec['high']} high, {sec['medium']} medium"),
        check("documentation exists in the workspace", bool(doc_files), ", ".join(doc_files[:6])),
        check("every risk has a validation plan", all(r["validation"] for r in risk["risks"]), f"{len(risk['risks'])} risks"),
        check("audit chain intact", chain_ok, chain_msg),
        check("human approvals recorded for high-impact stages", approvals is None, approvals or "all present"),
        check("change footprint within policy", len(impl["files"]) <= k.policy.max_files, f"{len(impl['files'])} files <= {k.policy.max_files}"),
        check("assumptions acknowledged", not assumptions, f"{len(assumptions)} recorded in the summary", required=False),
    ]
    go = all(c["ok"] for c in checklist if c["required"])
    version = k.spec["release"]["version"]
    notes = [
        f"# Release notes - {k.spec['title']} v{version}", "", f"Decision: **{'GO' if go else 'NO-GO'}**", "",
        f"Stack: {design['stack']['language']} / {design['stack']['framework']} ({design['stack']['build_tool']})", "", "## Changes",
        *[f"- `{f['path']}` ({f['status']}, +{f['added']}/-{f['removed']})" for f in impl["files"] if f["status"] != "unchanged"], "",
        "## Checklist", *[f"- [{'x' if c['ok'] else ' '}] {c['name']}: {c['detail']}" for c in checklist], "", "## Rollback plan", design["rollback_plan"], "",
    ]
    (k.run_dir / "release_notes.md").write_text("\n".join(notes), encoding="utf-8")
    decision = "GO" if go else "NO-GO"
    return {"decision": decision, "version": version, "checklist": checklist, "notes": "release_notes.md", "summary": f"{decision} v{version}: {sum(c['ok'] for c in checklist)}/{len(checklist)} checks"}


# ---- schema validation of subagent results ---------------------------------------------------------

_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "number": (int, float)}


def validate(data, schema, path="$") -> list[str]:
    """Validate data against a JSON Schema (type, enum, required, properties, items, additionalProperties). Returns a list of problems;
    an empty list means valid. Used on every subagent result.

    Example:
        validate({"a": 1}, {"type": "object", "required": ["a", "b"]})          -> ["$: missing 'b'"]
        validate({"a": "x"}, {"type": "object", "properties": {"a": {"type": "integer"}}})  -> ["$.a: expected integer, got str"]
        validate({"a": 1, "b": 2}, {"type": "object", "required": ["a", "b"]}) -> []
    """
    errors = []
    if "enum" in schema and data not in schema["enum"]:
        return [f"{path}: {data!r} not in {schema['enum']}"]
    t = schema.get("type")
    if t:
        ok = isinstance(data, _TYPES[t]) and not (t in ("integer", "number") and isinstance(data, bool))
        if not ok:
            return [f"{path}: expected {t}, got {type(data).__name__}"]
    if t == "object":
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{path}: missing '{key}'")
        props = schema.get("properties", {})
        for key, value in data.items():
            if key in props:
                errors += validate(value, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected '{key}'")
    if t == "array":
        for i, item in enumerate(data):
            errors += validate(item, schema.get("items", {}), f"{path}[{i}]")
    return errors
