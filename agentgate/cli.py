"""Command-line entry point.

    python -m agentgate demo
    python -m agentgate create task.json
    python -m agentgate start T-0001 --workspace path/to/project
    python -m agentgate dispatch T-0001
    python -m agentgate submit T-0001
    python -m agentgate validate T-0001
    python -m agentgate decide T-0001 --decision approved --by alice --rationale "looks right"
    python -m agentgate close T-0001
    python -m agentgate run task.json --workspace path/to/project   # all steps in one go
    python -m agentgate status | show T-0001

Real models are plugged in with two environment variables (see the README):
    AGENTGATE_WORKER_CMD     command that takes the prompt on stdin and edits files in the workspace
    AGENTGATE_REVIEWER_CMD   command that takes the prompt on stdin and answers APPROVE or REJECT
Optional: AGENTGATE_WORKER_FAMILY, AGENTGATE_REVIEWER_FAMILY (must differ).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .adapters import CommandReviewer, CommandWorker
from .routing import RoutingError, Router
from .schema import TaskValidationError
from .supervisor import Supervisor, SupervisorError


def _router(worker_name: str) -> Router:
    workers, reviewers = [], []
    if os.environ.get("AGENTGATE_WORKER_CMD"):
        workers.append(CommandWorker(worker_name, os.environ["AGENTGATE_WORKER_CMD"],
                                     family=os.environ.get("AGENTGATE_WORKER_FAMILY", "worker")))
    if os.environ.get("AGENTGATE_REVIEWER_CMD"):
        reviewers.append(CommandReviewer("reviewer", os.environ["AGENTGATE_REVIEWER_CMD"],
                                         family=os.environ.get("AGENTGATE_REVIEWER_FAMILY", "reviewer")))
    return Router(workers, reviewers)


def _supervisor(args, worker_name: str = "worker") -> Supervisor:
    return Supervisor(args.store, _router(worker_name))


def _show(task) -> None:
    print(f"{task.task_id}  {task.status:<20} risk={task.risk_level:<8} {task.title}")


def _cmd_demo(args) -> int:
    from .demo import main as demo_main
    return demo_main()


def _cmd_create(args) -> int:
    task = _supervisor(args).create_task(json.loads(Path(args.file).read_text("utf-8")))
    _show(task)
    for flag in task.policy_flags:
        print(f"  policy: {flag}")
    return 0


def _cmd_start(args) -> int:
    _show(_supervisor(args).start_task(args.task_id, args.workspace))
    return 0


def _cmd_dispatch(args) -> int:
    sup = _supervisor(args)
    name = args.worker or sup.get(args.task_id).assigned_worker
    task = _supervisor(args, name).dispatch_task(args.task_id, name)
    _show(task)
    print(f"  claim: {task.worker_claim[:200]!r}")
    return 0


def _cmd_submit(args) -> int:
    task = _supervisor(args).submit_task(args.task_id)
    _show(task)
    print(f"  review: {task.review['verdict']} - {task.review['reason']}")
    return 0


def _cmd_validate(args) -> int:
    task = _supervisor(args).validate_task(args.task_id)
    _show(task)
    for c in task.validation["checks"]:
        print(f"  [{c['status']:>5}] {c['name']}: {c['detail']}")
    return 0


def _cmd_decide(args) -> int:
    _show(_supervisor(args).decide_task(args.task_id, args.decision, args.by, args.rationale))
    return 0


def _cmd_close(args) -> int:
    _show(_supervisor(args).close_task(args.task_id))
    return 0


def _cmd_run(args) -> int:
    raw = json.loads(Path(args.file).read_text("utf-8"))
    sup = _supervisor(args, args.worker or raw.get("assigned_worker", "worker"))
    task = sup.create_task(raw)
    _show(task)
    if task.status == "blocked":
        return 1
    task = sup.run_task(task.task_id, args.workspace, args.worker)
    _show(task)
    return 0 if task.status == "done" else 1


def _cmd_status(args) -> int:
    tasks = _supervisor(args).list_tasks()
    if not tasks:
        print("(no tasks)")
    for t in tasks:
        _show(t)
    return 0


def _cmd_show(args) -> int:
    print(json.dumps(_supervisor(args).get(args.task_id).to_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentgate", description=__doc__.split("\n")[0])
    p.add_argument("--store", default=".agentgate", help="directory for task state (default: .agentgate)")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, *, file=False, task_id=False, workspace=False, worker=False, help=None):
        sp = sub.add_parser(name, help=help)
        if file:
            sp.add_argument("file")
        if task_id:
            sp.add_argument("task_id")
        if workspace:
            sp.add_argument("--workspace", required=True)
        if worker:
            sp.add_argument("--worker", default=None)
        sp.set_defaults(func=func)
        return sp

    add("demo", _cmd_demo, help="run the offline three-run demo")
    add("create", _cmd_create, file=True)
    add("start", _cmd_start, task_id=True, workspace=True)
    add("dispatch", _cmd_dispatch, task_id=True, worker=True)
    add("submit", _cmd_submit, task_id=True, help="diff, then the second-model review gate")
    add("validate", _cmd_validate, task_id=True)
    d = add("decide", _cmd_decide, task_id=True)
    d.add_argument("--decision", required=True, choices=["approved", "rejected", "needs_changes"])
    d.add_argument("--by", required=True)
    d.add_argument("--rationale", required=True)
    add("close", _cmd_close, task_id=True)
    add("run", _cmd_run, file=True, workspace=True, worker=True, help="all lifecycle steps in one go")
    add("status", _cmd_status)
    add("show", _cmd_show, task_id=True)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        code = args.func(args)
    except (SupervisorError, RoutingError, TaskValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)
