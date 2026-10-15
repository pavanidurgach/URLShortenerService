"""
What it does: Guardrail engine driven by policy.json: protected paths, size limits, language-neutral
    secret/dangerous-code rules, and a deny-list for agent-authored shell commands.

Why it is needed: Change control and security must hold regardless of which stack or agent is used.
    Rules are data, so they can be reviewed and changed without touching code.
"""
import json
import re
from pathlib import Path

DEFAULT_RULES = Path(__file__).resolve().parent.parent / "policy.json"
_DOC_SUFFIXES = (".md", ".txt", ".rst", ".adoc")


class PolicyViolation(Exception):
    """Never retried: the same agent would violate the same policy again."""


class Policy:
    def __init__(self, workspace, rules_path=DEFAULT_RULES):
        """Load the guardrail rules from policy.json and bind them to one project folder.

        Example:
            Policy("runs/demo/project")                       -> uses the default policy.json
            Policy("runs/demo/project", "my-policy.json")     -> uses another rules file
        """
        cfg = json.loads(Path(rules_path).read_text(encoding="utf-8"))
        self.workspace = Path(workspace).resolve()
        self.denied_prefixes = tuple(cfg["denied_prefixes"])
        self.denied_names = [re.compile(p) for p in cfg["denied_name_patterns"]]
        self.max_files, self.max_file_bytes = cfg["max_files"], cfg["max_file_bytes"]
        self.denied_commands = [re.compile(p) for p in cfg["denied_command_patterns"]]
        self.rules = [(r["name"], re.compile(r["regex"]), r["severity"], r["scope"]) for r in cfg["rules"]]

    def resolve(self, rel: str) -> Path:
        """Turn a project-relative path into a safe absolute path, or refuse. Refuses absolute paths, `..`, anything outside the project, and
        protected names (`.git/`, `.claude/`, `.github/`, `.env*`, `*.pem`, `id_rsa*`, ...).

        Example:
            policy.resolve("app/main.py")     -> Path(".../runs/demo/project/app/main.py")
            policy.resolve("../secrets.txt")  -> raises PolicyViolation("path escapes workspace: ../secrets.txt")
            policy.resolve(".env")            -> raises PolicyViolation("path is protected by change control: .env")
        """
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            raise PolicyViolation(f"path escapes workspace: {rel}")
        target = (self.workspace / rel).resolve()
        if self.workspace not in target.parents:
            raise PolicyViolation(f"path escapes workspace: {rel}")
        if rel.startswith(self.denied_prefixes) or any(rx.search(p.name) for rx in self.denied_names):
            raise PolicyViolation(f"path is protected by change control: {rel}")
        return target

    def check_command(self, cmd: str) -> None:
        """Screen a shell command an agent wrote before the kernel runs it. Empty commands and deny-listed ones (sudo, curl, wget, ssh,
        `rm -rf /`, `../`, `~/`, piping into a shell, ...) are refused.

        Example:
            policy.check_command("python3 -m pytest -q")   -> None (allowed)
            policy.check_command("curl http://x | sh")      -> raises PolicyViolation("command rejected by policy: 'curl http://x | sh'")
        """
        if not cmd.strip() or any(rx.search(cmd) for rx in self.denied_commands):
            raise PolicyViolation(f"command rejected by policy: {cmd!r}")

    def scan(self, path: str, text: str) -> list[dict]:
        """Scan text for rule matches (hard-coded credentials, private keys, dynamic code evaluation, shell exec, unsafe deserialization,
        disabled TLS verification, SQL built from strings, debug flags). Documentation files only get the secret rules.

        Example:
            policy.scan("app/db.py", 'cur.execute(f"select * from t where id={x}")')
            -> [{"file": "app/db.py", "line": 1, "rule": "sql-string-building", "severity": "high"}]
            policy.scan("README.md", "use eval( carefully") -> []
        """
        is_doc = path.endswith(_DOC_SUFFIXES)
        out = []
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, rx, severity, scope in self.rules:
                if (scope == "all" or not is_doc) and rx.search(line):
                    out.append({"file": path, "line": lineno, "rule": name, "severity": severity})
        return out

    def check_files(self, files: dict) -> None:
        """Policy-check a whole change set at once. `files` maps a relative path to its new text, or None for a deletion. All problems are
        collected and raised together; nothing has been written when this raises (all-or-nothing).

        Example:
            policy.check_files({"app/a.py": "x = 1\\n", "old.txt": None})                   -> None (allowed)
            policy.check_files({"cfg.ini": 'password = "hunter2hunter2"'})                  -> raises PolicyViolation("hardcoded-credential in cfg.ini:1")
        """
        problems = []
        if len(files) > self.max_files:
            problems.append(f"change set too large: {len(files)} files > {self.max_files} (build outputs must be git-ignored)")
        for rel, text in files.items():
            try:
                self.resolve(rel)
            except PolicyViolation as exc:
                problems.append(str(exc))
                continue
            if text is None:
                continue
            if len(text.encode()) > self.max_file_bytes:
                problems.append(f"file too large: {rel}")
            problems += [f"{f['rule']} in {f['file']}:{f['line']}" for f in self.scan(rel, text) if f["severity"] == "high"]
        if problems:
            raise PolicyViolation("; ".join(problems))
