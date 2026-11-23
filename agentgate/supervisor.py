"""The supervisor: owns the fixed lifecycle and the task store.

    create -> start -> dispatch -> submit -> validate -> decide -> close

* create    validate the task, apply policy (a violating task is born 'blocked')
* start     snapshot the workspace (the baseline) and move to in_progress
* dispatch  hand the task to a cheap-tier worker; keep its claim
* submit    diff against the baseline, run the review gate; APPROVE moves on,
            REJECT ends the task
* validate  scope check, tests-pass check, no-secret check
* decide    approve / reject / needs_changes; automatic approval only for low risk
* close     approved -> done

Task state is kept in <store>/tasks.json, so each step can be a separate CLI call.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import validators, workspace as ws
from .adapters.base import BlockedError
from .adapters.reviewer import ReviewError, ReviewRequest
from .policies import AUTO_APPROVER, DEFAULT_POLICY, Policy, check_task_policy, requires_human_decision
from .routing import Router, diff_digest
from .schema import Task, TaskValidationError, check_transition, now_iso

DECISIONS = {"approved": "approved", "rejected": "rejected", "needs_changes": "validation_failed"}


class SupervisorError(Exception):
    pass


class Supervisor:
    def __init__(self, store_dir: str | Path, router: Router, policy: Policy = DEFAULT_POLICY):
        self.store = Path(store_dir)
        self.store.mkdir(parents=True, exist_ok=True)
        self.router = router
        self.policy = policy

    # -- storage ----------------------------------------------------------
    @property
    def _db(self) -> Path:
        return self.store / "tasks.json"

    def list_tasks(self) -> list[Task]:
        if not self._db.exists():
            return []
        return [Task.from_dict(d) for d in json.loads(self._db.read_text("utf-8"))]

    def get(self, task_id: str) -> Task:
        for t in self.list_tasks():
            if t.task_id == task_id:
                return t
        raise SupervisorError(f"no such task {task_id}")

    def _save(self, task: Task) -> None:
        tasks = {t.task_id: t for t in self.list_tasks()}
        tasks[task.task_id] = task
        data = [t.to_dict() for _, t in sorted(tasks.items())]
        tmp = self._db.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._db)

    def _move(self, task: Task, new: str, actor: str, reason: str | None = None) -> None:
        errors = check_transition(task.status, new)
        if errors:
            raise SupervisorError("; ".join(errors))
        task.note(task.status, new, actor, reason)
        task.status = new

    def _expect(self, task: Task, *statuses: str) -> None:
        if task.status not in statuses:
            raise SupervisorError(f"{task.task_id} is {task.status!r}; this step needs {' or '.join(statuses)}")

    # -- lifecycle --------------------------------------------------------
    def create_task(self, raw: dict) -> Task:
        task_id = f"T-{len(self.list_tasks()) + 1:04d}"
        task = Task.from_raw(raw, task_id)          # may raise TaskValidationError
        task.policy_flags = check_task_policy(task, self.policy)
        if task.policy_flags:
            task.status = "blocked"
            task.note("created", "blocked", "supervisor", "; ".join(task.policy_flags))
        else:
            task.note("created", "pending", "supervisor")
        self._save(task)
        return task

    def start_task(self, task_id: str, workspace: str | Path) -> Task:
        task = self.get(task_id)
        self._expect(task, "pending")
        workspace = Path(workspace).resolve()
        if not workspace.is_dir():
            raise SupervisorError(f"workspace {workspace} is not a directory")
        baseline = self.store / "baselines" / task.task_id
        ws.take_baseline(workspace, baseline)
        task.workspace, task.baseline_dir = str(workspace), str(baseline)
        self._move(task, "in_progress", "supervisor", "baseline captured")
        self._save(task)
        return task

    def rework_task(self, task_id: str) -> Task:
        """After a failed validation: back to in_progress, keeping the original baseline."""
        task = self.get(task_id)
        self._expect(task, "validation_failed")
        self._move(task, "in_progress", "supervisor", "rework")
        self._save(task)
        return task

    def build_prompt(self, task: Task) -> str:
        return "\n".join([
            f"Task {task.task_id}: {task.title}", "", task.goal, "",
            f"You may edit only these files: {task.allowed_files}",
            f"You must not touch: {task.forbidden_files}",
            "If the task needs any other file, stop and report instead of editing it.",
            f"The work is done when these commands succeed: {task.acceptance_checks}",
            f"How to undo this change: {task.rollback_note}", "",
            "Do not commit or push. Make the change and stop; validation and review happen after you.",
        ])

    def dispatch_task(self, task_id: str, worker: str | None = None,
                      poll_interval: float = 0.2, max_wait: float = 600) -> Task:
        task = self.get(task_id)
        self._expect(task, "in_progress")
        adapter = self.router.pick_worker(worker or task.assigned_worker)
        deadline = time.monotonic() + max_wait
        try:
            handle = adapter.dispatch(self.build_prompt(task), Path(task.workspace))
            while adapter.poll(handle) == "running":
                if time.monotonic() > deadline:
                    raise BlockedError(f"worker still running after {max_wait}s")
                time.sleep(poll_interval)
        except BlockedError as exc:
            self._move(task, "blocked", adapter.name, f"worker blocked: {exc.reason}")
            self._save(task)
            return task
        task.worker_family = adapter.family
        task.worker_claim = adapter.fetch_result(handle)
        task.history.append(f"{now_iso()} | {task.task_id} | dispatched to {adapter.name} ({adapter.tier} tier)")
        self._save(task)
        return task

    def submit_task(self, task_id: str) -> Task:
        """The review gate. APPROVE -> ready_for_validation, REJECT -> rejected."""
        task = self.get(task_id)
        self._expect(task, "in_progress")
        baseline, workdir = Path(task.baseline_dir), Path(task.workspace)
        diff = ws.unified_diff(baseline, workdir)
        digest = diff_digest(diff)
        reviewer = self.router.pick_reviewer(task.worker_family)
        self.router.claim_review(task, digest)       # refuses a repeat review of identical work
        evidence = validators.tests_pass(task, workdir)
        try:
            verdict = reviewer.review(ReviewRequest(task, diff, task.worker_claim, evidence))
        except ReviewError as exc:
            task.reviewed_digests.remove(digest)     # no verdict was given, so nothing was spent
            self._save(task)
            raise SupervisorError(f"review gate gave no verdict: {exc}") from exc
        task.review = {"reviewer": reviewer.name, "tier": reviewer.tier, "verdict": verdict.word,
                       "reason": verdict.reason, "diff_digest": digest}
        self._move(task, "ready_for_validation" if verdict.approved else "rejected",
                   reviewer.name, f"{verdict.word}: {verdict.reason}")
        self._save(task)
        return task

    def validate_task(self, task_id: str) -> Task:
        task = self.get(task_id)
        self._expect(task, "ready_for_validation")
        task.validation = validators.run_all(task, Path(task.baseline_dir), Path(task.workspace), self.policy)
        overall = task.validation["overall"]
        self._move(task, "ready_for_review" if overall == "pass" else "validation_failed",
                   "validator", f"overall={overall}")
        self._save(task)
        return task

    def decide_task(self, task_id: str, decision: str, by: str, rationale: str) -> Task:
        task = self.get(task_id)
        self._expect(task, "ready_for_review")
        if decision not in DECISIONS:
            raise SupervisorError(f"decision must be one of {sorted(DECISIONS)}")
        if not rationale.strip() or not by.strip():
            raise SupervisorError("a decision needs a decider and a rationale")
        if decision == "approved" and by == AUTO_APPROVER and requires_human_decision(task, self.policy):
            raise SupervisorError(f"{task.task_id} (risk={task.risk_level}) needs a human decision; "
                                  f"{AUTO_APPROVER!r} may not approve it")
        task.decisions.append({"decision": decision, "by": by, "rationale": rationale, "at": now_iso()})
        self._move(task, DECISIONS[decision], by, rationale)
        self._save(task)
        return task

    def auto_decide(self, task_id: str) -> Task:
        """Approve automatically if policy allows; otherwise leave it waiting for a human."""
        task = self.get(task_id)
        if requires_human_decision(task, self.policy):
            return task
        return self.decide_task(task_id, "approved", AUTO_APPROVER, "low risk, review gate and validators passed")

    def close_task(self, task_id: str) -> Task:
        task = self.get(task_id)
        self._expect(task, "approved")
        self._move(task, "done", "supervisor")
        self._save(task)
        return task

    def run_task(self, task_id: str, workspace: str | Path, worker: str | None = None) -> Task:
        """Walk one task through the whole lifecycle, stopping at the first gate that says no."""
        self.start_task(task_id, workspace)
        task = self.dispatch_task(task_id, worker)
        if task.status != "in_progress":
            return task
        task = self.submit_task(task_id)
        if task.status != "ready_for_validation":
            return task
        task = self.validate_task(task_id)
        if task.status != "ready_for_review":
            return task
        task = self.auto_decide(task_id)
        return self.close_task(task_id) if task.status == "approved" else task


__all__ = ["Supervisor", "SupervisorError", "TaskValidationError"]
