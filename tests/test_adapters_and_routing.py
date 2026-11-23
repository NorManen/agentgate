import sys

import pytest

from agentgate.adapters import (
    BlockedError, CommandReviewer, CommandWorker, FakeReviewer, FakeWorker, ReviewError,
    ReviewRequest, parse_verdict,
)
from agentgate.routing import RepeatReviewError, Router, RoutingError
from agentgate.schema import Task


def test_fake_worker_writes_and_deletes(tmp_path):
    (tmp_path / "gone.txt").write_text("x")
    worker = FakeWorker("w", {"a/b.txt": "hi", "gone.txt": None}, "done")
    handle = worker.dispatch("prompt", tmp_path)
    assert worker.poll(handle) == "done" and worker.fetch_result(handle) == "done"
    assert (tmp_path / "a" / "b.txt").read_text() == "hi" and not (tmp_path / "gone.txt").exists()


def test_command_worker_runs_in_workdir_and_reads_prompt(tmp_path):
    script = "import sys; open('out.txt','w').write(sys.stdin.read()); print('wrote it')"
    worker = CommandWorker("cmd", [sys.executable, "-c", script])
    handle = worker.dispatch("hello prompt", tmp_path)
    assert (tmp_path / "out.txt").read_text() == "hello prompt"
    assert worker.fetch_result(handle) == "wrote it"


def test_command_worker_blocks_on_missing_or_failing_command(tmp_path):
    with pytest.raises(BlockedError):
        CommandWorker("c", ["no-such-command-xyz"]).dispatch("p", tmp_path)
    with pytest.raises(BlockedError):
        CommandWorker("c", [sys.executable, "-c", "raise SystemExit(3)"]).dispatch("p", tmp_path)


def test_parse_verdict():
    assert parse_verdict("APPROVE looks fine").approved
    assert not parse_verdict("reject: out of scope").approved
    for bad in ("", "Maybe approve", "I think APPROVE"):
        with pytest.raises(ReviewError):
            parse_verdict(bad)


def request(raw_task, diff="+x\n", claim="done", status="pass"):
    return ReviewRequest(Task.from_raw(raw_task, "T-0001"), diff, claim, {"status": status, "detail": "d"})


def test_fake_reviewer_rules(raw_task):
    r = FakeReviewer()
    assert r.review(request(raw_task)).approved
    assert not r.review(request(raw_task, diff="")).approved
    assert not r.review(request(raw_task, status="fail")).approved


def test_fake_reviewer_rejects_out_of_scope_file_and_names_it(raw_task):
    one = "--- a/discount.py\n+++ b/discount.py\n+x\n"
    diff = one + "--- a/test_discount.py\n+++ b/test_discount.py\n+y\n"
    verdict = FakeReviewer().review(request(raw_task, diff=diff))
    assert not verdict.approved and "test_discount.py" in verdict.reason
    other = "--- a/other.py\n+++ b/other.py\n+y\n"
    assert "other.py" in FakeReviewer().review(request(raw_task, diff=other)).reason
    assert FakeReviewer().review(request(raw_task, diff=one)).approved


def test_command_reviewer_parses_and_fails_closed(raw_task):
    ok = CommandReviewer("r", [sys.executable, "-c", "print('APPROVE fine')"])
    assert ok.review(request(raw_task)).approved
    no = CommandReviewer("r", [sys.executable, "-c", "print('REJECT bad')"])
    assert not no.review(request(raw_task)).approved
    vague = CommandReviewer("r", [sys.executable, "-c", "print('hmm')"])
    with pytest.raises(ReviewError):
        vague.review(request(raw_task))
    crash = CommandReviewer("r", [sys.executable, "-c", "raise SystemExit(1)"])
    with pytest.raises(ReviewError):
        crash.review(request(raw_task))


def test_router_refuses_premium_worker_and_unknown_worker():
    premium = FakeWorker("big", {}, "", tier="premium")
    router = Router([premium], [FakeReviewer()])
    with pytest.raises(RoutingError):
        router.pick_worker("big")
    with pytest.raises(RoutingError):
        router.pick_worker("nobody")


def test_router_reviewer_must_be_premium_and_other_family():
    cheap_reviewer = FakeReviewer(tier="cheap")
    same_family = FakeReviewer(family="fam")
    router = Router([], [cheap_reviewer, same_family])
    with pytest.raises(RoutingError):
        router.pick_reviewer("fam")
    assert router.pick_reviewer("other") is same_family


def test_router_refuses_repeat_review_of_same_diff(raw_task):
    task = Task.from_raw(raw_task, "T-0001")
    Router.claim_review(task, "abc")
    Router.claim_review(task, "def")
    with pytest.raises(RepeatReviewError):
        Router.claim_review(task, "abc")
