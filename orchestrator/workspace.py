"""
What it does: Workspace operations: list project files (respecting the project's own .gitignore),
    create an isolated staging copy, collect its diff, apply the diff atomically after a policy
    check, and snapshot/restore for rollback.

Why it is needed: Agents never edit the real project directly. Changes are staged, checked, then
    applied all-or-nothing, and can be undone exactly.
"""
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .policy import PolicyViolation


def _git(cwd, *args) -> str:
    """Run a git command in a folder and return its output (git is used only as a file lister/differ; identity is a fixed placeholder).

    Example:
        _git("runs/demo/project", "ls-files", "--others", "--exclude-standard") -> "app/main.py\\ntests/test_api.py\\n"
    """
    cmd = ["git", "-c", "user.name=orchestrator", "-c", "user.email=orchestrator@local", "-c", "core.quotepath=off", *args]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout


def project_files(ws: Path) -> list[str]:
    """The project's files: everything not ignored by the project's own .gitignore (so dependency folders and build output are excluded).
    Creates a git index in the folder if needed.

    Example:
        project_files("runs/demo/project") -> ["README.md", "app/main.py", "requirements.txt", "tests/test_api.py"]
        (empty folder)                -> []
    """
    ws = Path(ws)
    if not (ws / ".git").exists():
        _git(ws, "init", "-q")
    return sorted(f for f in _git(ws, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if f and not f.startswith(".git/"))


def project_mode(ws: Path) -> str:
    """Is the folder a new project or existing code? Decided from whether it has any project files.

    Example:
        project_mode(empty folder)               -> "new-project"
        project_mode(folder with app/main.py)    -> "existing-project"
    """
    return "existing-project" if project_files(ws) else "new-project"


def write_atomic(path: Path, text: str) -> None:
    """Write a file so readers never see a half-written one (write to a temp file in the same folder, then rename). Creates parent folders.

    Example:
        write_atomic(Path("runs/demo/project/app/main.py"), "print('hi')\\n")  -> file exists with exactly that content
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _prune(policy, path: Path) -> None:
    """After deleting a file, remove its parent folders if they are now empty (stopping at the project root).

    Example:
        (deleted docs/api/old.md, docs/api/ is empty) -> docs/api/ is removed; docs/ stays if it still has files
    """
    for parent in path.parents:
        if parent == policy.workspace:
            break
        try:
            parent.rmdir()
        except OSError:
            break


def _restore(policy, snapshot: dict) -> None:
    """Put files back to a saved state: a text value rewrites the file; None deletes a file that did not exist before.

    Example:
        _restore(policy, {"app/main.py": "old text", "app/new.py": None})
        -> app/main.py has "old text"; app/new.py is deleted
    """
    for rel, old in snapshot.items():
        target = policy.resolve(rel)
        if old is None:
            if target.exists():
                target.unlink()
                _prune(policy, target)
        else:
            write_atomic(target, old)


def snapshot(policy, run_dir: Path, name: str, rels) -> dict:
    """Record the pre-change content of the files a stage is about to touch, in snapshot_<stage>.json. Kept across re-runs: on a re-run it
    first restores the original state, then also tracks any files the new run adds.

    Example:
        snapshot(policy, run_dir, "implement", ["app/main.py", "app/new.py"])
        -> {"app/main.py": "<current text>", "app/new.py": None}   (None = did not exist)
    """
    path = Path(run_dir) / f"snapshot_{name}.json"
    snap = json.loads(path.read_text()) if path.exists() else {}
    _restore(policy, snap)
    for rel in rels:
        if rel not in snap:
            snap[rel] = t.read_text(encoding="utf-8") if (t := policy.resolve(rel)).exists() else None
    path.write_text(json.dumps(snap))
    return snap


def rollback_snapshot(policy, run_dir: Path, name: str) -> bool:
    """Undo a stage: restore its snapshot and delete the snapshot file. Returns whether there was anything to roll back.

    Example:
        rollback_snapshot(policy, run_dir, "implement") -> True   (files restored)
        rollback_snapshot(policy, run_dir, "tests")     -> False  (that stage never wrote files)
    """
    path = Path(run_dir) / f"snapshot_{name}.json"
    if not path.exists():
        return False
    _restore(policy, json.loads(path.read_text()))
    path.unlink()
    return True


def restore_pre_change(policy, run_dir: Path, name: str) -> None:
    """Return the project to the state before a stage's earlier run (used before re-running a write-capable stage), keeping the snapshot.

    Example:
        restore_pre_change(policy, run_dir, "implement") -> project files back to pre-implement state; snapshot file remains
    """
    path = Path(run_dir) / f"snapshot_{name}.json"
    if path.exists():
        _restore(policy, json.loads(path.read_text()))


def make_staging(ws: Path, run_dir: Path, stage_id: str) -> Path:
    """Create an isolated copy of the project for an agent to edit: copies the project files, initialises a git baseline so the agent's
    changes can be diffed later. The real project is untouched.

    Example:
        make_staging("runs/demo/project", "runs/demo", "implement") -> Path("runs/demo/staging/implement")
    """
    staging = Path(run_dir) / "staging" / stage_id
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for rel in project_files(ws):
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(ws) / rel, dest)
    _git(staging, "init", "-q")
    _git(staging, "add", "-A")
    _git(staging, "commit", "-q", "--allow-empty", "-m", "baseline")
    return staging


def collect_changes(staging: Path) -> dict:
    """Diff a staging copy against its baseline. Returns relative path -> new text, or None for a deleted file. Binary files and symlinks
    are refused.

    Example:
        collect_changes("runs/demo/staging/implement")
        -> {"app/main.py": "<new text>", "tests/test_api.py": "<new text>", "old_notes.txt": None}
    """
    _git(staging, "add", "-A")
    raw = _git(staging, "-c", "status.renames=false", "status", "--porcelain=v1", "-z")
    changes = {}
    for entry in filter(None, raw.split("\0")):
        code, rel = entry[:2], entry[3:]
        path = Path(staging) / rel
        if "D" in code:
            changes[rel] = None
        elif path.is_symlink():
            raise PolicyViolation(f"symlinks are not allowed: {rel}")
        else:
            try:
                changes[rel] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise PolicyViolation(f"binary file in change set: {rel}") from None
    return changes


def apply_changes(policy, run_dir: Path, stage_id: str, changes: dict) -> dict:
    """Apply a staged change set to the real project: policy check first (all-or-nothing), then snapshot, then atomic writes/deletes.
    Returns the per-file record with added/removed line counts.

    Example:
        apply_changes(policy, run_dir, "implement", {"app/main.py": "print(1)\\n"})
        -> {"files": [{"path": "app/main.py", "status": "added", "sha256": "ab12cd34ef56", "added": 1, "removed": 0}],
            "counts": {"added": 1, "modified": 0, "deleted": 0, "unchanged": 0}, "lines_added": 1, "lines_removed": 0}
        (a file contains a hard-coded key) -> raises PolicyViolation and writes nothing
    """
    policy.check_files(changes)
    snap = snapshot(policy, run_dir, stage_id, changes)
    records, added, removed = [], 0, 0
    for rel, text in sorted(changes.items()):
        old = snap.get(rel)
        new_lines = (text or "").splitlines()
        a = sum(1 for l in difflib.ndiff((old or "").splitlines(), new_lines) if l.startswith("+ "))
        r = sum(1 for l in difflib.ndiff((old or "").splitlines(), new_lines) if l.startswith("- "))
        target = policy.resolve(rel)
        if text is None:
            status = "deleted"
            if target.exists():
                target.unlink()
                _prune(policy, target)
        else:
            status = "added" if old is None else "unchanged" if old == text else "modified"
            write_atomic(target, text)
        records.append({"path": rel, "status": status, "sha256": hashlib.sha256((text or "").encode()).hexdigest()[:12], "added": a, "removed": r})
        added, removed = added + a, removed + r
    counts = {s: sum(r["status"] == s for r in records) for s in ("added", "modified", "deleted", "unchanged")}
    return {"files": records, "counts": counts, "lines_added": added, "lines_removed": removed}
