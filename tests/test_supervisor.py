import pytest

from agentgate.adapters import FakeReviewer, FakeWorker
from agentgate.demo import FIXED, LEAKED, WEAKENED_TESTS, WRONG_FIX
from agentgate.routing import RepeatReviewError
from agentgate.supervisor import SupervisorError


def test_full_lifecycle_good_worker(make_supervisor, good_worker, raw_task, workspace):
    sup = make_supervisor(good_worker)
    task = sup.create_task(raw_task)
    assert task.status == "pending"
    assert sup.start_task(task.task_id, workspace).status == "in_progress"
    assert sup.dispatch_task(task.task_id).status == "in_progress"
    assert sup.submit_task(task.task_id).status == "ready_for_validation"
    assert sup.validate_task(task.task_id).status == "ready_for_review"
    assert sup.auto_decide(task.task_id).status == "approved"
    final = sup.close_task(task.task_id)
    assert final.status == "done" and final.decisions[0]["by"] == "auto-policy"
    assert len(final.history) >= 7


def test_state_survives_between_calls(make_supervisor, good_worker, raw_task, workspace):
    sup = make_supervisor(good_worker)
    task = sup.create_task(raw_task)
    sup.start_task(task.task_id, workspace)
    assert make_supervisor(good_worker).get(task.task_id).status == "in_progress"


def test_scope_violation_is_rejected_by_review_gate(make_supervisor, raw_task, workspace):
    worker = FakeWorker("cheap-worker", {"discount.py": FIXED, "test_discount.py": WEAKENED_TESTS}, "Tests pass.")
    task = make_supervisor(worker).run_task(make_supervisor(worker).create_task(raw_task).task_id, workspace)
    assert task.status == "rejected" and "test_discount.py" in task.review["reason"]


def test_scope_violation_is_stopped_by_validator_even_if_reviewer_approves(
        make_supervisor, raw_task, workspace):
    from conftest import ApproveEverything
    worker = FakeWorker("cheap-worker", {"discount.py": FIXED, "test_discount.py": WEAKENED_TESTS}, "Tests pass.")
    sup = make_supervisor(worker, ApproveEverything())
    task = sup.run_task(sup.create_task(raw_task).task_id, workspace)
    assert task.status == "validation_failed"
    scope = next(c for c in task.validation["checks"] if c["name"] == "scope")
    assert scope["status"] == "fail" and "test_discount.py" in scope["detail"]


def test_false_success_claim_is_rejected_by_gate(make_supervisor, raw_task, workspace):
    worker = FakeWorker("cheap-worker", {"discount.py": WRONG_FIX}, "All fixed, tests pass.")
    sup = make_supervisor(worker)
    task = sup.run_task(sup.create_task(raw_task).task_id, workspace)
    assert task.status == "rejected" and task.review["verdict"] == "REJECT"
    assert task.validation is None          # never reached validation


def test_no_change_is_rejected(make_supervisor, raw_task, workspace):
    sup = make_supervisor(FakeWorker("cheap-worker", {}, "done"))
    assert sup.run_task(sup.create_task(raw_task).task_id, workspace).status == "rejected"


def test_secret_added_by_worker_fails_validation(make_supervisor, raw_task, workspace):
    sup = make_supervisor(FakeWorker("cheap-worker", {"discount.py": LEAKED}, "Tests pass."))
    task = sup.run_task(sup.create_task(raw_task).task_id, workspace)
    assert task.status == "validation_failed"
    assert any(c["name"] == "secrets" and c["status"] == "fail" for c in task.validation["checks"])


def test_policy_violating_task_is_born_blocked(make_supervisor, good_worker, raw_task):
    sup = make_supervisor(good_worker)
    task = sup.create_task({**raw_task, "allowed_files": ["config/.env"]})
    assert task.status == "blocked" and task.policy_flags
    with pytest.raises(SupervisorError):
        sup.start_task(task.task_id, ".")


def test_steps_cannot_be_skipped(make_supervisor, good_worker, raw_task, workspace):
    sup = make_supervisor(good_worker)
    task = sup.create_task(raw_task)
    for step in (sup.dispatch_task, sup.submit_task, sup.validate_task, sup.close_task):
        with pytest.raises(SupervisorError):
            step(task.task_id)


@pytest.mark.parametrize("risk", ["medium", "high", "critical"])
def test_auto_policy_cannot_approve_above_low_risk(make_supervisor, good_worker, raw_task, workspace, risk):
    sup = make_supervisor(good_worker)
    task = sup.create_task({**raw_task, "risk_level": risk})
    sup.start_task(task.task_id, workspace)
    sup.dispatch_task(task.task_id)
    sup.submit_task(task.task_id)
    sup.validate_task(task.task_id)
    assert sup.auto_decide(task.task_id).status == "ready_for_review"   # waits for a human
    with pytest.raises(SupervisorError):
        sup.decide_task(task.task_id, "approved", "auto-policy", "try")
    done = sup.decide_task(task.task_id, "approved", "a human", "checked the diff")
    assert done.status == "approved"
    assert sup.close_task(task.task_id).status == "done"


def test_needs_changes_then_rework_then_repeat_review_refused(make_supervisor, raw_task, workspace):
    worker = FakeWorker("cheap-worker", {"discount.py": FIXED, "test_discount.py": WEAKENED_TESTS}, "Tests pass.")
    from conftest import ApproveEverything
    sup = make_supervisor(worker, ApproveEverything())
    task = sup.create_task(raw_task)
    sup.start_task(task.task_id, workspace)
    sup.dispatch_task(task.task_id)
    sup.submit_task(task.task_id)
    assert sup.validate_task(task.task_id).status == "validation_failed"
    sup.rework_task(task.task_id)
    # same unchanged work submitted again: the premium review is not bought twice
    with pytest.raises(RepeatReviewError):
        sup.submit_task(task.task_id)
    # after a real change, review is allowed again
    (workspace / "test_discount.py").write_text((workspace.parent / "work" / "test_discount.py").read_text() + "\n# touched\n")
    assert sup.submit_task(task.task_id).status == "ready_for_validation"


def test_blocked_worker_blocks_task(make_supervisor, raw_task, workspace):
    from agentgate.adapters import CommandWorker
    sup = make_supervisor(CommandWorker("cheap-worker", ["no-such-command-xyz"]))
    task = sup.run_task(sup.create_task(raw_task).task_id, workspace)
    assert task.status == "blocked"


def test_reviewer_error_gives_no_approval_and_spends_nothing(make_supervisor, good_worker, raw_task, workspace):
    class Broken(FakeReviewer):
        def review(self, request):
            from agentgate.adapters import ReviewError
            raise ReviewError("no answer")

    sup = make_supervisor(good_worker, Broken())
    task = sup.create_task(raw_task)
    sup.start_task(task.task_id, workspace)
    sup.dispatch_task(task.task_id)
    with pytest.raises(SupervisorError):
        sup.submit_task(task.task_id)
    after = sup.get(task.task_id)
    assert after.status == "in_progress" and after.reviewed_digests == []


def test_premium_worker_is_refused(make_supervisor, raw_task, workspace):
    from agentgate.routing import RoutingError
    sup = make_supervisor(FakeWorker("cheap-worker", {}, "", tier="premium"))
    task = sup.create_task(raw_task)
    sup.start_task(task.task_id, workspace)
    with pytest.raises(RoutingError):
        sup.dispatch_task(task.task_id)
