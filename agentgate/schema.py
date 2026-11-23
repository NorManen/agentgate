"""Task record, statuses and the legal status transitions.

A task is a scoped unit of work: it lists the files a worker may change, the
files it must not touch, the commands that prove it is done, and a risk level.
This module has no side effects.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

RISK_LEVELS = ("low", "medium", "high", "critical")

STATUSES = (
    "pending",               # created, not started
    "in_progress",           # a worker is (or may be) editing the workspace
    "ready_for_validation",  # worker output passed the review gate
    "validation_failed",     # validators found a problem; needs rework
    "ready_for_review",      # validators passed; waiting for a decision
    "approved",              # decision recorded: approved
    "rejected",              # review gate or decision said no
    "blocked",               # policy problem or worker could not run
    "done",                  # approved and closed
)

TERMINAL_STATUSES = {"rejected", "done", "blocked"}

_LEGAL = {
    "pending": {"in_progress", "blocked"},
    "in_progress": {"ready_for_validation", "rejected", "blocked"},
    "ready_for_validation": {"validation_failed", "ready_for_review", "blocked"},
    "validation_failed": {"in_progress", "blocked"},
    "ready_for_review": {"approved", "rejected", "validation_failed", "blocked"},
    "approved": {"done"},
}

REQUIRED_FIELDS = (
    "title", "goal", "allowed_files", "forbidden_files",
    "acceptance_checks", "risk_level", "rollback_note",
)


class TaskValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_transition(current: str, new: str) -> list[str]:
    """Return a list of errors; empty means the move is legal."""
    if current not in STATUSES or new not in STATUSES:
        return [f"unknown status in {current!r} -> {new!r}"]
    if current in TERMINAL_STATUSES:
        return [f"task is {current!r} and cannot move to {new!r}"]
    if new not in _LEGAL.get(current, set()):
        return [f"illegal transition {current!r} -> {new!r}"]
    return []


@dataclass
class Task:
    task_id: str
    title: str
    goal: str
    allowed_files: list[str]
    forbidden_files: list[str]
    acceptance_checks: list[str]
    risk_level: str
    rollback_note: str
    assigned_worker: str = "worker"
    protected_override: bool = False
    status: str = "pending"
    policy_flags: list[str] = field(default_factory=list)
    workspace: str | None = None
    baseline_dir: str | None = None
    worker_family: str | None = None
    worker_claim: str = ""
    reviewed_digests: list[str] = field(default_factory=list)
    review: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    decisions: list[dict[str, Any]] = field(default_factory=list)
    history: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: dict[str, Any], task_id: str) -> "Task":
        errors = _validate(raw)
        if errors:
            raise TaskValidationError(errors)
        return cls(
            task_id=task_id,
            title=raw["title"],
            goal=raw["goal"],
            allowed_files=list(raw["allowed_files"]),
            forbidden_files=list(raw["forbidden_files"]),
            acceptance_checks=list(raw["acceptance_checks"]),
            risk_level=raw["risk_level"],
            rollback_note=raw["rollback_note"],
            assigned_worker=raw.get("assigned_worker", "worker"),
            protected_override=bool(raw.get("protected_override", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(**data)

    def note(self, from_status: str, to_status: str, actor: str, reason: str | None = None) -> None:
        line = f"{now_iso()} | {self.task_id} | {from_status} -> {to_status} | {actor}"
        if reason:
            line += f" | {reason}"
        self.history.append(line)


def _validate(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        errors.append(f"missing required fields: {missing}")
    for name in ("title", "goal", "rollback_note"):
        if name in raw and (not isinstance(raw[name], str) or not raw[name].strip()):
            errors.append(f"{name} must be a non-empty string")
    for name in ("allowed_files", "forbidden_files", "acceptance_checks"):
        if name in raw and not isinstance(raw[name], list):
            errors.append(f"{name} must be a list")
    if isinstance(raw.get("allowed_files"), list) and not raw["allowed_files"]:
        errors.append("allowed_files must not be empty: a task must declare its scope")
    if isinstance(raw.get("acceptance_checks"), list) and not raw["acceptance_checks"]:
        errors.append("acceptance_checks must not be empty: a task needs a way to prove it is done")
    if "risk_level" in raw and raw["risk_level"] not in RISK_LEVELS:
        errors.append(f"risk_level must be one of {list(RISK_LEVELS)}")
    return errors
