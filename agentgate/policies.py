"""Policy: which files are protected, and who may approve what.

Rules:
* Files matching `never_touch` can never be in a task's scope.
* Files matching `protected` need `protected_override: true` AND risk "critical".
* Automatic approval is possible only for risk "low" with no policy flags and no
  protected file in scope. Everything else needs a human decision.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from .schema import Task

AUTO_APPROVER = "auto-policy"
AUTO_APPROVE_MAX_RISK = "low"


@dataclass(frozen=True)
class Policy:
    never_touch: tuple[str, ...] = ("*.env", ".env", "*.pem", "*.key", "secrets/*")
    protected: tuple[str, ...] = (".github/*", "deploy/*", "migrations/*", "pyproject.toml")


DEFAULT_POLICY = Policy()


def matches(patterns, path: str) -> bool:
    path = path.replace("\\", "/")
    return any(fnmatch.fnmatch(path, p) or path == p for p in patterns)


def check_task_policy(task: Task, policy: Policy = DEFAULT_POLICY) -> list[str]:
    """Static check of a task's declared scope. Empty list means no violations."""
    problems: list[str] = []
    for f in task.allowed_files:
        if matches(policy.never_touch, f):
            problems.append(f"allowed_files includes {f!r}, which no task may ever touch")
        elif matches(policy.protected, f):
            if not task.protected_override:
                problems.append(f"allowed_files includes protected file {f!r} without protected_override")
            elif task.risk_level != "critical":
                problems.append(f"protected file {f!r} needs risk_level 'critical', got {task.risk_level!r}")
    overlap = sorted(set(task.allowed_files) & set(task.forbidden_files))
    if overlap:
        problems.append(f"allowed_files and forbidden_files overlap: {overlap}")
    return problems


def check_scope(changed: list[str], task: Task, policy: Policy = DEFAULT_POLICY) -> list[str]:
    """Compare files a worker really changed with the task's declared scope."""
    problems: list[str] = []
    forbidden = set(task.forbidden_files) | set(policy.never_touch)
    for f in changed:
        if matches(forbidden, f):
            problems.append(f"{f} is forbidden")
        elif matches(policy.protected, f) and not task.protected_override:
            problems.append(f"{f} is protected and has no override")
        elif not matches(task.allowed_files, f):
            problems.append(f"{f} is not in allowed_files")
    return problems


def requires_human_decision(task: Task, policy: Policy = DEFAULT_POLICY) -> bool:
    if task.risk_level != AUTO_APPROVE_MAX_RISK:
        return True
    if task.policy_flags:
        return True
    return any(matches(policy.protected, f) for f in task.allowed_files)
