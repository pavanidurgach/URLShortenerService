"""
What it does: Tests for the kernel's governance: refusals, parallel batches and joins, schema
    validation, retry/fallback/triage/rollback, policy violations, independent test re-run, safe-
    stop/resume, amend/early cutoff, audit tampering and metrics.

Why it is needed: These are the guarantees the system claims; the tests prove the kernel enforces
    them without relying on a model.
"""
import hashlib
import json
from pathlib import Path

import pytest

from orchestrator import workspace as wsp
from orchestrator.audit import read_log, verify_log
from orchestrator.kernel import Kernel, KernelError
from tests.sim import SimTeam, drive, start

ROOT = Path(__file__).resolve().parent.parent


def tree(ws: Path) -> dict:
    return {rel: hashlib.sha256((ws / rel).read_bytes()).hexdigest() for rel in wsp.project_files(ws)}


def events(k, name=None):
    return [r for r in read_log(k.audit.path) if name is None or r["event"] == name]


@pytest.fixture
def built(tmp_path):
    """A finished run that built a new project in tmp_path/project; later runs on the same folder change existing code."""
    team = SimTeam()
    k = start(tmp_path, "url-shortener")
    assert drive(k, team)["status"] == "SUCCEEDED"
    return tmp_path, team, k


def test_governance_cannot_be_skipped(tmp_path):
    k = start(tmp_path, "url-shortener")
    with pytest.raises(KernelError, match="not ready"):
        k.start("design")  # dependencies unfinished
    team = SimTeam()
    while "implement" not in [r["stage"] for r in k.next().get("ready", [])]:
        drive_step = k.next()
        for item in drive_step["ready"]:
            if item["kind"] == "human":
                k.start(item["stage"]); k.complete(item["stage"])
            else:
                s = k.start(item["stage"]); team.perform(item["stage"], s); k.complete(item["stage"])
    with pytest.raises(KernelError, match="high-impact.*approval"):
        k.start("implement")
    with pytest.raises(KernelError, match="--by"):
        k.approve("implement")  # who decided must be recorded
    with pytest.raises(KernelError, match="not high-impact"):
        k.approve("tests", by="x")


def test_three_requirements_in_a_row_new_build_change_and_vague_all_succeed(tmp_path):
    team = SimTeam()
    runs = {}
    for name in ("url-shortener", "link-expiry-and-analytics", "make-it-safer-and-faster"):  # same project folder: later runs change existing code
        runs[name] = start(tmp_path, name)
        assert drive(runs[name], team)["status"] == "SUCCEEDED", name
        k = runs[name]
        assert verify_log(k.run_dir / "audit.jsonl")[0]
        summary = (k.run_dir / "FINAL_SUMMARY.md").read_text()
        for heading in ("Requirement and normalised problem", "Plan and rationale", "Orchestration", "Subagent runs", "Risks, trade-offs, validation", "Limitations"):
            assert heading in summary
        assert "GO" in (k.run_dir / "release_notes.md").read_text()
    assert sorted(tree(tmp_path / "project")) == [".gitignore", "config.sh", "docs/README.md", "shorten.sh", "test.sh", "tests/test_core.sh", "tests/test_expiry.sh"]
    assert runs["url-shortener"].get("design")["stack"]["language"] == "sh"  # stack came from the design agent, not the kernel
    assert not (tmp_path / "project" / "build").exists()  # .gitignore'd build output never entered the project


def test_independent_stages_are_released_as_parallel_batches_with_joins(tmp_path):
    team = SimTeam()
    start(tmp_path, "url-shortener")
    drive(Kernel(tmp_path / "url-shortener", ROOT), team)
    k = start(tmp_path, "link-expiry-and-analytics")
    team.batches.clear()
    drive(k, team)
    assert ["impact", "risk"] in [sorted(b) for b in team.batches]  # parallel paths
    assert any({"docs", "security"} <= set(b) for b in team.batches)  # tests joins them too, except where a fault drill fails it before delegation
    order = [e["stage"] for e in events(k, "stage_succeeded")]
    assert order.index("release") > max(order.index(s) for s in ("tests", "security", "docs"))  # synchronisation join


def test_plan_waives_only_optional_stages_and_guidance_reaches_briefs(built):
    out, team, k = built
    assert k.state["impact"] == "WAIVED" and "impact" not in k.artifacts and team.count("impact") == 0
    assert [e["details"]["target"] for e in events(k, "plan_applied")] == ["impact"]
    assert "keep it minimal" in (k.run_dir / "briefs" / "implement.md").read_text()
    b = start(out, "link-expiry-and-analytics")
    drive(b, team)
    assert b.state["implement"] == "DONE" and events(b, "plan_rejected")[0]["details"]["target"] == "implement"  # not optional -> refused


def test_clarify_stage_is_spawned_at_runtime_and_amend_replans_only_affected_stages(tmp_path):
    team = SimTeam()
    for n in ("url-shortener", "link-expiry-and-analytics"):
        drive(start(tmp_path, n), team)
    k = start(tmp_path, "make-it-safer-and-faster")
    assert "clarify" not in k.wf.stages
    assert drive(k, team)["status"] == "SUCCEEDED"
    assert "clarify" in k.wf.stages and "clarify" in k.wf.stages["design"].deps
    m = k.metrics()
    assert m["fallbacks"] == 1 and m["replans"] >= 3 and m["approvals_granted"] == 4  # implement + release approved twice (re-approval after amend)
    assert "RATE_LIMIT=30" in (tmp_path / "project" / "config.sh").read_text()  # the late change request reached the code
    arts = k.artifacts
    assert arts["requirements"].version == 1 and arts["risk"].version == 1 and arts["clarify"].version == 2
    assert arts["design"].version == 2 and arts["implement"].version == 2


def test_early_cutoff_when_a_rerun_produces_identical_output(built):
    out, team, k = built
    k.amend("risk", {"summary": k.get("risk")["summary"]}, "tester", "no-op change")  # same content -> same digest
    assert not k.next().get("ready") or all(i["stage"] != "design" for i in k.next()["ready"])


def test_unattended_run_cannot_perform_high_impact_actions(tmp_path):
    team, k = SimTeam(), start(tmp_path, "url-shortener", scripted=False)
    for _ in range(20):  # advance until the first approval is needed
        r = k.next()
        item = next((i for i in r["ready"] if i["needs_approval"]), None)
        if item:
            break
        for i in r["ready"]:
            s = k.start(i["stage"])
            if s.get("action") == "ask_human":
                k.complete(i["stage"], answers={}, by="tester")
            else:
                team.perform(i["stage"], s); k.complete(i["stage"])
    assert item["stage"] == "implement"
    res = k.approve("implement", by="reviewer", reject=True)
    assert res["status"] == "HALTED" and k.state["implement"] == "REJECTED" and team.count("implement") == 0
    assert wsp.project_files(tmp_path / "project") == []


def test_bad_agent_output_is_rejected_by_schema_and_retried(tmp_path):
    team = SimTeam()
    team.overrides["risk"] = lambda cwd, brief, result: {"summary": "x"}  # missing 'risks'
    k = start(tmp_path, "url-shortener")
    res = drive(k, team)
    assert res["status"] == "FAILED" and "risk" in res["failed_stage"]
    assert any("schema" in e["details"]["error"] for e in events(k, "stage_failed"))


def test_retries_are_bounded_then_fallback_then_triage(tmp_path):
    team = SimTeam()
    k = start(tmp_path, "url-shortener", faults={"docs": 2})
    assert drive(k, team)["status"] == "SUCCEEDED"
    assert k.agent_used["docs"] == "technical-writer-lite" and len(events(k, "stage_retry")) == 1 and len(events(k, "stage_fallback")) == 1
    # triage retry is bounded to one supervised round
    team2, k2 = SimTeam(), start(tmp_path / "b", "url-shortener", faults={"tests": 99})
    team2.triage_action = "retry"
    res = drive(k2, team2)
    assert res["status"] == "FAILED" and len(events(k2, "triage_decision")) == 1  # second exhaustion is not re-triaged
    assert k2.guidance["tests"] == "check the build output first"  # the triager's advice is carried into the stage's next brief


@pytest.mark.parametrize("action,status", [("rollback", "FAILED"), ("escalate", "HALTED")])
def test_triage_rollback_and_escalate(built, action, status):
    out, team, _ = built
    before = tree(out / "project")
    team.triage_action = action
    k = start(out, "link-expiry-and-analytics", faults={"tests": 99})
    res = drive(k, team)
    assert res["status"] == status
    assert events(k, "triage_decision")[0]["details"]["action"] == action
    if action == "rollback":
        assert tree(out / "project") == before and k.state["implement"] == "ROLLED_BACK"
    else:  # escalate keeps the work and can be resumed after a human intervenes
        assert k.state["tests"] == "PENDING" and k.state["implement"] == "DONE"
        resumed = start(out, "link-expiry-and-analytics", resume=True)
        resumed.faults["tests"] = 0
        resumed.triaged["tests"] = 1
        assert drive(resumed, team)["status"] == "SUCCEEDED"


def test_policy_violations_are_never_retried_or_triaged_and_nothing_is_written(tmp_path):
    team = SimTeam()

    def leak(cwd, brief, result):
        (cwd / "ok.txt").write_text("fine\n")
        (cwd / "leak.cfg").write_text('api_key = "sk-live-0123456789abcdef"\n')
        return {"summary": "s", "test_command_used": "", "tests_passed": True, "iterations": 1, "notes": ""}

    team.overrides["implement"] = leak
    k = start(tmp_path, "url-shortener")
    res = drive(k, team)
    assert res["status"] == "FAILED" and events(k, "policy_violation") and not events(k, "triage_decision") and not events(k, "stage_retry")
    assert wsp.project_files(tmp_path / "project") == []


def test_agent_claiming_green_cannot_override_the_independent_rerun(tmp_path):
    team = SimTeam()
    orig = team.on_implement

    def break_tests(cwd, brief, result):
        out = orig(cwd, brief, result)
        (cwd / "tests" / "test_core.sh").write_text("exit 1\n")  # real failure while the tests subagent still reports 3 passed
        return out

    team.overrides["implement"] = break_tests
    k = start(tmp_path, "url-shortener")
    res = drive(k, team)
    assert res["status"] == "FAILED" and res["failed_stage"] == "tests"
    assert "independent exit code" in json.dumps(read_log(k.audit.path))
    assert wsp.project_files(tmp_path / "project") == []  # rolled back


def test_safe_stop_and_resume_do_not_repeat_completed_work(tmp_path):
    team = SimTeam()
    k = start(tmp_path, "url-shortener")
    s = k.start("plan"); team.perform("plan", s); k.complete("plan")
    assert k.stop("operator")["status"] == "HALTED"
    assert k.next()["status"] == "HALTED"
    with pytest.raises(KernelError):
        k.start("requirements")
    resumed = start(tmp_path, "url-shortener", resume=True)
    assert drive(resumed, team)["status"] == "SUCCEEDED" and team.count("plan") == 1
    assert verify_log(resumed.audit.path)[0]


def test_kill_switch_file_halts_the_run(tmp_path):
    k = start(tmp_path, "url-shortener")
    (tmp_path / "STOP").write_text("x")
    assert k.next()["status"] == "HALTED"


def test_audit_log_detects_tampering(built):
    _, _, k = built
    path = k.audit.path
    assert verify_log(path) == (True, "ok")
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:3] + [lines[3].replace('"event"', '"evnt"')] + lines[4:]) + "\n")
    assert verify_log(path)[0] is False
    path.write_text("\n".join(lines[:2] + lines[3:]) + "\n")
    assert verify_log(path)[0] is False


def test_metrics_are_derived_from_the_audit_log(tmp_path):
    team = SimTeam()
    k = start(tmp_path, "url-shortener", faults={"tests": 1})
    drive(k, team)
    m = k.metrics()
    assert m["retries"] == 1 and m["mttr_seconds"] is not None and m["run_success_rate"] == 1.0
    assert m["agent_runs"] > 0 and m["agent_tokens"] > 0 and m["e2e_latency_seconds"] >= 0


def test_every_delegated_run_is_counted_even_when_usage_is_not_reported(tmp_path):
    team = SimTeam()
    k = start(tmp_path, "url-shortener")
    s = k.start("plan"); team.perform("plan", s); k.complete("plan")  # no --usage
    runs = events(k, "agent_run")
    assert len(runs) == 1 and runs[0]["details"] == {} and k.metrics()["agent_runs"] == 1 and k.metrics()["agent_tokens"] == 0


def test_every_run_gets_a_workflow_document_that_tracks_runtime_changes(tmp_path):
    k = start(tmp_path, "make-it-safer-and-faster")
    doc = (k.run_dir / "WORKFLOW.md").read_text()
    assert "```mermaid" in doc and "implement 🔒" in doc and "| design |" in doc and "**PENDING**" in doc  # exists from the first moment
    assert 'clarify["clarify"]' not in doc and "| clarify |" not in doc  # not spawned yet
    drive(k, SimTeam())
    doc = (k.run_dir / "WORKFLOW.md").read_text()
    assert "Status: **SUCCEEDED**" in doc and "class plan done" in doc and "class impact waived" in doc  # colour = final state
    assert 'clarify["clarify"]' in doc and "| clarify | human | human |" in doc  # the stage spawned at runtime is documented


def test_default_layout_puts_the_project_inside_its_run_folder_and_survives_a_fresh_init(tmp_path):
    cfg = json.loads((ROOT / "examples" / "inputs" / "url-shortener.config.json").read_text())
    k = Kernel.create(ROOT / "examples" / "inputs" / "url-shortener.md", tmp_path, ROOT, config=cfg, scripted=True)
    assert k.workspace == (tmp_path / "url-shortener" / "project").resolve() and k.workspace.is_dir()
    assert not (tmp_path / "project").exists()  # no sibling project folder next to the run folders
    drive(k, SimTeam())
    built = tree(k.workspace)
    assert built and (k.run_dir / "audit.jsonl").exists()
    again = Kernel.create(ROOT / "examples" / "inputs" / "url-shortener.md", tmp_path, ROOT, config=cfg, scripted=True)
    assert tree(again.workspace) == built and again.mode == "existing-project"  # the built software is kept
    assert not (again.run_dir / "staging").exists() and len(read_log(again.audit.path)) <= 2  # run files were reset


def test_staging_copies_are_deleted_after_success_and_kept_after_failure(tmp_path):
    ok = start(tmp_path / "ok", "url-shortener")
    assert drive(ok, SimTeam())["status"] == "SUCCEEDED"
    assert not (ok.run_dir / "staging").exists()  # implement and docs staging copies are gone, the project has the files
    assert wsp.project_files(ok.workspace)

    team = SimTeam()
    team.overrides["implement"] = lambda cwd, brief, result: (cwd / "leak.cfg").write_text('api_key = "sk-live-0123456789abcdef"\n') or {"summary": "s", "test_command_used": "", "tests_passed": True, "iterations": 1, "notes": ""}
    bad = start(tmp_path / "bad", "url-shortener")
    assert drive(bad, team)["status"] == "FAILED"
    assert (bad.run_dir / "staging" / "implement" / "leak.cfg").exists()  # kept for debugging


def test_a_retry_keeps_the_previous_attempts_work_and_tells_the_agent_why_it_was_rejected(tmp_path):
    team, calls = SimTeam(), {"n": 0}
    orig = team.on_implement

    def flaky_result(cwd, brief, result):
        calls["n"] += 1
        if calls["n"] == 1:
            orig(cwd, brief, result)  # attempt 1 writes the code ...
            return {"summary": "done", "project_dir": "x", "files_changed": ["a"]}  # ... but returns a malformed result
        assert (cwd / "shorten.sh").exists(), "attempt 2 must find attempt 1's code in the staging copy"
        assert "previous attempt was rejected" in brief and "does not match the schema" in brief and "continue from it" in brief
        return {"summary": "fixed the result only", "test_command_used": "sh test.sh", "tests_passed": True, "iterations": 1, "notes": ""}

    team.overrides["implement"] = flaky_result
    k = start(tmp_path, "url-shortener")
    assert drive(k, team)["status"] == "SUCCEEDED" and calls["n"] == 2
    assert "shorten.sh" in wsp.project_files(k.workspace) and "previous attempt" not in (k.run_dir / "briefs" / "tests.md").read_text()
