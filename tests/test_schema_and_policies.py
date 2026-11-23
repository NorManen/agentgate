import pytest

from agentgate.policies import (
    AUTO_APPROVER, check_scope, check_task_policy, requires_human_decision,
)
from agentgate.schema import Task, TaskValidationError, check_transition


def make(raw_task, **changes):
    return Task.from_raw({**raw_task, **changes}, "T-0001")


def test_valid_task_builds(raw_task):
    task = make(raw_task)
    assert task.status == "pending" and task.allowed_files == ["discount.py"]


@pytest.mark.parametrize("change", [
    {"allowed_files": []},
    {"acceptance_checks": []},
    {"risk_level": "extreme"},
    {"rollback_note": "  "},
])
def test_bad_task_is_rejected(raw_task, change):
    with pytest.raises(TaskValidationError):
        make(raw_task, **change)


def test_missing_field_is_rejected(raw_task):
    del raw_task["goal"]
    with pytest.raises(TaskValidationError):
        make(raw_task)


def test_legal_path_and_illegal_jump():
    assert check_transition("pending", "in_progress") == []
    assert check_transition("pending", "done")
    assert check_transition("done", "in_progress")      # terminal
    assert check_transition("in_progress", "approved")  # cannot skip the gates


def test_never_touch_file_is_flagged(raw_task):
    assert check_task_policy(make(raw_task, allowed_files=["app/.env"], forbidden_files=[]))


def test_protected_file_needs_override_and_critical(raw_task):
    base = dict(allowed_files=["deploy/run.sh"], forbidden_files=[])
    assert check_task_policy(make(raw_task, **base))
    assert check_task_policy(make(raw_task, protected_override=True, risk_level="high", **base))
    assert not check_task_policy(make(raw_task, protected_override=True, risk_level="critical", **base))


def test_allowed_forbidden_overlap_is_flagged(raw_task):
    assert check_task_policy(make(raw_task, forbidden_files=["discount.py"]))


def test_scope_check_flags_forbidden_and_unlisted(raw_task):
    task = make(raw_task)
    assert check_scope(["discount.py"], task) == []
    problems = check_scope(["test_discount.py", "other.py"], task)
    assert len(problems) == 2


def test_scope_glob_matches_subpaths(raw_task):
    task = make(raw_task, allowed_files=["src/*"], forbidden_files=[])
    assert check_scope(["src/a/b.py"], task) == []


@pytest.mark.parametrize("risk", ["medium", "high", "critical"])
def test_only_low_risk_can_be_automatic(raw_task, risk):
    assert requires_human_decision(make(raw_task, risk_level=risk))


def test_low_risk_is_automatic(raw_task):
    assert not requires_human_decision(make(raw_task))
    assert AUTO_APPROVER == "auto-policy"
