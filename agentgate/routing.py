"""Cost routing.

* Implementation goes to the cheap tier. A premium-tier worker is refused.
* Review goes to the premium tier, and must come from a different model family
  than the worker, so a model never checks its own output.
* The same unchanged work is never reviewed twice: a repeat review of an
  identical diff is refused instead of buying another vote.
"""
from __future__ import annotations

import hashlib

from .adapters.base import WorkerAdapter
from .adapters.reviewer import Reviewer
from .schema import Task


class RoutingError(Exception):
    pass


class RepeatReviewError(RoutingError):
    """The diff is identical to one that was already reviewed."""


def diff_digest(diff_text: str) -> str:
    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest()[:16]


class Router:
    def __init__(self, workers: list[WorkerAdapter], reviewers: list[Reviewer],
                 implement_tier: str = "cheap", review_tier: str = "premium"):
        self.workers = {w.name: w for w in workers}
        self.reviewers = list(reviewers)
        self.implement_tier = implement_tier
        self.review_tier = review_tier

    def pick_worker(self, name: str) -> WorkerAdapter:
        worker = self.workers.get(name)
        if worker is None:
            raise RoutingError(f"no worker named {name!r}; known: {sorted(self.workers)}")
        if worker.tier != self.implement_tier:
            raise RoutingError(
                f"worker {name!r} is tier {worker.tier!r}; implementation is routed to {self.implement_tier!r}")
        return worker

    def pick_reviewer(self, worker_family: str | None) -> Reviewer:
        for reviewer in self.reviewers:
            if reviewer.tier == self.review_tier and reviewer.family != worker_family:
                return reviewer
        raise RoutingError(
            f"no {self.review_tier!r} reviewer from a different family than {worker_family!r}")

    @staticmethod
    def claim_review(task: Task, digest: str) -> None:
        """Record that this diff is being reviewed; refuse if it already was."""
        if digest in task.reviewed_digests:
            raise RepeatReviewError(f"diff {digest} was already reviewed; change the work before asking again")
        task.reviewed_digests.append(digest)
