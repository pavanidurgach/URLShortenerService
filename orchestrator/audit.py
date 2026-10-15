"""
What it does: Append-only, hash-chained audit log (audit.jsonl) plus a verifier. Each record
    includes the hash of the previous one.

Why it is needed: Provides tamper-evident traceability: editing, deleting or reordering any record
    is detected. Metrics and the final report are derived from this log.

Also holds compute_metrics(): reliability metrics (success rate, retries, rollbacks, approvals, re-
    plans, MTTR, latency, subagent runs) derived only from this log, so they cannot disagree with
    the evidence.
"""
import hashlib
import json
import threading
import time
from collections import defaultdict
from pathlib import Path

GENESIS = "0" * 64


def _digest(record: dict) -> str:
    """SHA-256 of a log record, ignoring its own `hash` field. This is the link that chains records together.

    Example:
        _digest({"seq": 0, "event": "run_started", "prev": "0"*64, "hash": "ignored"})
        -> "9c1e...b27a"   (64 hex characters; the same for any value of "hash")
    """
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def read_log(path) -> list[dict]:
    """Read an audit file (one JSON record per line) into a list. A missing file gives an empty list.

    Example:
        read_log("runs/demo/audit.jsonl") -> [{"seq": 0, "event": "run_started", ...}, {"seq": 1, ...}]
        read_log("does/not/exist.jsonl")  -> []
    """
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def verify_log(path) -> tuple[bool, str]:
    """Check that the audit chain is intact: sequence numbers are 0,1,2,..., each record's `prev` equals the previous record's
    hash, and each record's own hash matches its content.

    Example:
        verify_log("runs/demo/audit.jsonl")            -> (True, "ok")
        (after editing record 3 by hand)                -> (False, "hash chain broken at record 3")
        (after deleting record 2)                       -> (False, "sequence gap at record 2")
    """
    prev = GENESIS
    for i, rec in enumerate(read_log(path)):
        if rec.get("seq") != i:
            return False, f"sequence gap at record {i}"
        if rec.get("prev") != prev or rec.get("hash") != _digest(rec):
            return False, f"hash chain broken at record {i}"
        prev = rec["hash"]
    return True, "ok"


class AuditLog:
    def __init__(self, path, run_id: str, listener=None):
        """Open (or continue) the audit log for one run. If the file already has records (resume), numbering and the hash chain continue
        from the last record.

        Example:
            AuditLog("runs/demo/audit.jsonl", run_id="demo")           -> a log whose next record is seq 0
            AuditLog(<existing file with 12 records>, run_id="demo")    -> a log whose next record is seq 12
        """
        self.listener = listener
        self.path = Path(path)
        self.run_id = run_id
        self._lock = threading.Lock()
        self._seq, self._prev = 0, GENESIS
        records = read_log(self.path)
        if records:  # resuming: continue the existing chain
            self._seq, self._prev = records[-1]["seq"] + 1, records[-1]["hash"]

    def append(self, event: str, stage: str | None = None, actor: str = "engine", **details) -> dict:
        """Append one event to the log, chained to the previous record, and return the stored record. Extra keyword arguments become `details`.
        Thread-safe.

        Example:
            log.append("stage_succeeded", "design", "agent:solution-designer", digest="ab12", duration=41.2)
            -> {"seq": 7, "event": "stage_succeeded", "stage": "design", "actor": "agent:solution-designer",
                "details": {"digest": "ab12", "duration": 41.2}, "prev": "<hash of record 6>", "hash": "<hash of this record>", ...}
        """
        with self._lock:
            rec = {
                "seq": self._seq,
                "ts": time.time(),
                "run_id": self.run_id,
                "event": event,
                "stage": stage,
                "actor": actor,
                "details": details,
                "prev": self._prev,
            }
            rec["hash"] = _digest(rec)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
            self._seq, self._prev = self._seq + 1, rec["hash"]
        if self.listener:
            self.listener(rec)
        return rec


# ---- reliability metrics, derived only from the log ----------------------------------------------------
def compute_metrics(records: list[dict]) -> dict:
    """Derive reliability metrics from a list of audit records (never from anything else, so metrics cannot disagree with the evidence).
    Includes success rates, retries, fallbacks, rollbacks, approvals, re-plans, MTTR (first failure of a stage -> its next success),
    end-to-end latency and subagent run counts.

    Example:
        compute_metrics(read_log("runs/demo/audit.jsonl"))
        -> {"attempt_success_rate": 0.9, "run_success_rate": 1.0, "retries": 1, "rollbacks": 0, "approvals_granted": 2,
            "mttr_seconds": 0.77, "e2e_latency_seconds": 462.0, "agent_runs": 8, "stage_latency_seconds": {"design": 41.2, ...}, ...}
    """
    c = defaultdict(int)
    stage_latency: dict = defaultdict(float)
    first_failure: dict = {}
    recoveries: list[float] = []
    run_open, e2e, outcomes = None, 0.0, []
    for r in records:
        ev, sid, ts, d = r["event"], r["stage"], r["ts"], r["details"]
        if ev == "run_started":
            run_open = ts
        elif ev == "run_finished":
            outcomes.append(d["status"])
            if run_open is not None:
                e2e += ts - run_open
            run_open = None
        elif ev == "stage_succeeded":
            c["attempts_succeeded"] += 1
            stage_latency[sid] += d["duration"]
            if sid in first_failure:
                recoveries.append(ts - first_failure.pop(sid))
        elif ev == "stage_failed":
            c["attempts_failed"] += 1
            first_failure.setdefault(sid, ts)
        elif ev == "agent_run":
            c["agent_runs"] += 1
            c["agent_tokens"] += d.get("tokens", 0)
            c["agent_tool_uses"] += d.get("tool_uses", 0)
        elif ev == "triage_decision":
            c["triage_decisions"] += 1
        elif ev == "stage_retry":
            c["retries"] += 1
        elif ev == "stage_fallback":
            c["fallbacks"] += 1
        elif ev == "stage_rolled_back":
            c["rollbacks"] += 1
        elif ev == "policy_violation":
            c["policy_violations"] += 1
        elif ev == "gate_blocked":
            c["gate_blocks"] += 1
        elif ev == "approval_decision":
            c["approvals_granted" if d["approved"] else "approvals_denied"] += 1
        elif ev in ("amended", "replan"):
            c["replans"] += 1
        elif ev == "stage_stale":
            c["stages_invalidated"] += 1
        elif ev == "early_cutoff":
            c["early_cutoffs"] += 1
    attempts = c["attempts_succeeded"] + c["attempts_failed"]
    return {
        "attempt_success_rate": round(c["attempts_succeeded"] / attempts, 3) if attempts else None,
        "run_success_rate": round(outcomes.count("SUCCEEDED") / len(outcomes), 3) if outcomes else None,
        "retries": c["retries"],
        "fallbacks": c["fallbacks"],
        "triage_decisions": c["triage_decisions"],
        "rollbacks": c["rollbacks"],
        "policy_violations": c["policy_violations"],
        "gate_blocks": c["gate_blocks"],
        "approvals_granted": c["approvals_granted"],
        "approvals_denied": c["approvals_denied"],
        "replans": c["replans"],
        "stages_invalidated": c["stages_invalidated"],
        "early_cutoffs": c["early_cutoffs"],
        "mttr_seconds": round(sum(recoveries) / len(recoveries), 4) if recoveries else None,
        "unrecovered_failures": len(first_failure),
        "agent_runs": c["agent_runs"],
        "agent_tokens": c["agent_tokens"],
        "agent_tool_uses": c["agent_tool_uses"],
        "e2e_latency_seconds": round(e2e, 3),
        "stage_latency_seconds": {k: round(v, 3) for k, v in sorted(stage_latency.items())},
    }
