"""
What it does: Command-line front end to the kernel: init, next, start, complete, fail, approve,
    triage, amend, stop, metrics, verify-audit, guard. Every command prints JSON; a refused
    step exits with code 2.

Why it is needed: It is the only way the orchestrator agent (and a human) can move a run forward, so
    each step goes through the kernel's governance checks.
"""
import argparse
import json
import sys
from pathlib import Path

from .audit import verify_log
from .guard import main as guard_main
from .kernel import Kernel, KernelError, ROOT


def _json(text, default=None):
    """Parse a JSON command-line argument, or return a default when it was not given.

    Example:
        _json('{"tokens": 1200}') -> {"tokens": 1200}
        _json(None)               -> None
        _json(None, {})           -> {}
    """
    return json.loads(text) if text else default


def _kernel(args) -> Kernel:
    """Open the kernel for an existing run: the directory given by --run, otherwise the run named in runs/ACTIVE.

    Example:
        _kernel(args with run="runs/url-shortener") -> Kernel for that run
        _kernel(args with run=None, no runs/ACTIVE)  -> raises KernelError("no active run: pass --run runs/<name>")
    """
    run = args.run
    if not run:
        pointer = ROOT / "runs" / "ACTIVE"
        if not pointer.exists():
            raise KernelError("no active run: pass --run runs/<name>")
        run = json.loads(pointer.read_text())["run_dir"]
    return Kernel(run, ROOT)


def main(argv=None) -> int:
    """Entry point of `python3 -m orchestrator`. Parses the command, runs it against the kernel and prints the JSON result. Returns the
    process exit code: 0 ok, 1 audit check failed, 2 a step the kernel refused.

    Example:
        main(["init", "examples/inputs/url-shortener.md", "--scripted", "--config", "examples/inputs/url-shortener.config.json"])
        -> prints {"run_dir": ".../runs/url-shortener", "workspace": ".../runs/url-shortener/project", "scripted": true, "stages": ["plan", ...]}; returns 0
        main(["start", "implement"])  (no approval recorded yet) -> prints {"refused": "stage 'implement' is high-impact: ..."}; returns 2
    """
    p = argparse.ArgumentParser(prog="orchestrator", description="Deterministic governance kernel for agentic SDLC runs")
    p.add_argument("--run", help="run directory (default: the active run)")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="start a governed run for a requirement file")
    i.add_argument("requirement", help="path to the requirement (markdown/text)")
    i.add_argument("--project", help="folder to build in / change (default: <out>/<name>/project, inside the run folder). An existing codebase there makes it a change to existing code")
    i.add_argument("--config", help="optional run config JSON: title, release_version, reviewer (scripted approvals), faults, amendment")
    i.add_argument("--name", help="run name (default: requirement file name)")
    i.add_argument("--out", default=str(ROOT / "runs"))
    i.add_argument("--scripted", action="store_true", help="use the config's simulated reviewer instead of asking a human")
    i.add_argument("--inject", action="append", metavar="STAGE:N", help="fault drill: fail STAGE's next N attempts before any agent is delegated")
    i.add_argument("--resume", action="store_true", help="continue a halted run of this requirement")
    sub.add_parser("next", help="what may happen now")
    sub.add_parser("status")
    sub.add_parser("metrics")
    s = sub.add_parser("start")
    s.add_argument("stage")
    c = sub.add_parser("complete")
    c.add_argument("stage")
    c.add_argument("--result")
    c.add_argument("--usage", help='JSON: {"tokens":N,"tool_uses":N,"duration_ms":N} as reported for the subagent run')
    c.add_argument("--answers", help="JSON {ambiguity_id: answer} (human stage)")
    c.add_argument("--note", default="")
    c.add_argument("--by", default="")
    f = sub.add_parser("fail")
    f.add_argument("stage")
    f.add_argument("--error", required=True)
    f.add_argument("--usage")
    a = sub.add_parser("approval", help="facts for the approval briefing")
    a.add_argument("stage")
    b = sub.add_parser("record-brief")
    b.add_argument("stage")
    b.add_argument("--summary", required=True)
    b.add_argument("--recommendation", required=True)
    ap = sub.add_parser("approve", help="record a human decision for a high-impact stage")
    ap.add_argument("stage")
    ap.add_argument("--by", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--reject", action="store_true")
    ap.add_argument("--scripted", action="store_true")
    t = sub.add_parser("triage")
    t.add_argument("stage")
    t.add_argument("--action", required=True)
    t.add_argument("--guidance", default="")
    t.add_argument("--rationale", default="")
    am = sub.add_parser("amend")
    am.add_argument("stage")
    am.add_argument("--patch", required=True)
    am.add_argument("--actor", required=True)
    am.add_argument("--reason", required=True)
    st = sub.add_parser("stop")
    st.add_argument("--reason", default="operator stop")
    v = sub.add_parser("verify-audit")
    v.add_argument("path")
    sub.add_parser("guard", help="PreToolUse hook entrypoint (reads the event on stdin)")
    args = p.parse_args(argv)

    if args.cmd == "guard":
        return guard_main()
    if args.cmd == "verify-audit":
        ok, msg = verify_log(args.path)
        print(json.dumps({"intact": ok, "detail": msg}))
        return 0 if ok else 1
    try:
        if args.cmd == "init":
            faults = {k: int(v) for k, v in (x.split(":") for x in args.inject or [])}
            cfg = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else None
            k = Kernel.create(args.requirement, args.out, ROOT, args.project, cfg, args.name, args.scripted, faults, args.resume)
            out = {"run_dir": str(k.run_dir), "workspace": str(k.workspace), "scripted": k.scripted, "stages": list(k.wf.stages)}
        else:
            k = _kernel(args)
            usage = _json(getattr(args, "usage", None))
            out = {
                "next": lambda: k.next(), "status": lambda: k.status_report(), "metrics": lambda: k.metrics(),
                "start": lambda: k.start(args.stage),
                "complete": lambda: k.complete(args.stage, args.result, usage, _json(args.answers), args.note, args.by),
                "fail": lambda: k.fail(args.stage, args.error, usage), "approval": lambda: k.approval_input(args.stage),
                "record-brief": lambda: k.record_brief(args.stage, args.summary, args.recommendation),
                "approve": lambda: k.approve(args.stage, args.by, args.note, args.reject, args.scripted),
                "triage": lambda: k.triage(args.stage, args.action, args.guidance, args.rationale),
                "amend": lambda: k.amend(args.stage, _json(args.patch), args.actor, args.reason),
                "stop": lambda: k.stop(args.reason),
            }[args.cmd]()
    except KernelError as exc:
        print(json.dumps({"refused": str(exc)}, indent=2))
        return 2
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
