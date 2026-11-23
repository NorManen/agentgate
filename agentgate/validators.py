"""Validators: scope check, tests-pass check, no-secret check, and the rollup.

Each check returns {"name", "status", "detail"} where status is
"pass", "fail" (a real problem found) or "error" (the check itself could not run).
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from . import workspace as ws
from .policies import DEFAULT_POLICY, Policy, check_scope
from .schema import Task


def _result(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def run_command(command, cwd: Path, timeout: float = 120, stdin_text: str | None = None):
    """Run a configured command without a shell. The token '{python}' means this interpreter."""
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    argv = [sys.executable if a == "{python}" else a for a in argv]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
        input=stdin_text if stdin_text is not None else "",
    )


def scope_check(task: Task, baseline: Path, workspace: Path, policy: Policy = DEFAULT_POLICY) -> dict[str, str]:
    changed = ws.changed_files(baseline, workspace)
    problems = check_scope(changed, task, policy)
    if problems:
        return _result("scope", "fail", "; ".join(problems))
    return _result("scope", "pass", f"changed: {', '.join(changed) or 'nothing'}")


def tests_pass(task: Task, workspace: Path, timeout: float = 120) -> dict[str, str]:
    summaries = []
    for command in task.acceptance_checks:
        try:
            proc = run_command(command, workspace, timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _result("tests", "error", f"could not run {command!r}: {exc}")
        lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
        summary = lines[-1].strip("= ") if lines else f"exit {proc.returncode}"
        summary = re.sub(r"\s+in \d+(\.\d+)?s\b.*$", "", summary)  # drop the run time: keeps output stable
        if proc.returncode != 0:
            return _result("tests", "fail", summary)
        summaries.append(summary)
    return _result("tests", "pass", "; ".join(summaries))


_SECRET_PATTERNS = {
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "cloud access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "token-like string": re.compile(r"\b(?:sk|ghp|xox[a-z])[-_][A-Za-z0-9_-]{20,}"),
    "assigned credential": re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']"),
}


def no_secret(diff_text: str) -> dict[str, str]:
    """Scan lines the worker ADDED. Reports where, never the matched value."""
    current, hits = "?", set()
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            current = line[6:]
        elif line.startswith("+"):
            for label, rx in _SECRET_PATTERNS.items():
                if rx.search(line):
                    hits.add(f"{label} in {current}")
    if hits:
        return _result("secrets", "fail", "; ".join(sorted(hits)))
    return _result("secrets", "pass", "no secret patterns in added lines")


def rollup(checks: list[dict[str, str]]) -> str:
    statuses = {c["status"] for c in checks}
    return "error" if "error" in statuses else "fail" if "fail" in statuses else "pass"


def run_all(task: Task, baseline: Path, workspace: Path, policy: Policy = DEFAULT_POLICY) -> dict:
    checks = [
        scope_check(task, baseline, workspace, policy),
        tests_pass(task, workspace),
        no_secret(ws.unified_diff(baseline, workspace)),
    ]
    return {"overall": rollup(checks), "checks": checks}
