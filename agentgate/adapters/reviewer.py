"""The second-model review gate: a reviewer must answer APPROVE or REJECT.

A reviewer sees the task scope, the diff, the worker's own claim, and the result of
running the acceptance checks. Anything that is not clearly APPROVE or REJECT is an
error, and an error never counts as approval.
"""
from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..policies import check_scope
from ..schema import Task
from ..validators import run_command


class ReviewError(Exception):
    """The reviewer could not give a usable verdict. Treated as 'not approved'."""


@dataclass
class ReviewRequest:
    task: Task
    diff: str
    claim: str
    evidence: dict[str, str]   # result of running the acceptance checks


@dataclass
class Verdict:
    approved: bool
    reason: str

    @property
    def word(self) -> str:
        return "APPROVE" if self.approved else "REJECT"


class Reviewer(ABC):
    name = "reviewer"
    tier = "premium"
    family = "unknown"

    @abstractmethod
    def review(self, request: ReviewRequest) -> Verdict: ...


def parse_verdict(text: str) -> Verdict:
    """The first word must be APPROVE or REJECT. Anything else raises ReviewError."""
    text = text.strip()
    first = text.split(None, 1)[0].strip(".:,;!").upper() if text else ""
    if first not in ("APPROVE", "REJECT"):
        raise ReviewError(f"unparseable verdict: {text[:120]!r}")
    reason = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
    return Verdict(first == "APPROVE", reason)


def build_prompt(req: ReviewRequest) -> str:
    t = req.task
    return (
        "You are reviewing a code change before it enters validation. Be skeptical: "
        "your job is to catch problems, not to be agreeable.\n\n"
        f"Task: {t.title}\nGoal: {t.goal}\n"
        f"Allowed files: {t.allowed_files}\nForbidden files: {t.forbidden_files}\n"
        f"Worker's own claim: {req.claim or '(none)'}\n"
        f"Acceptance check result: {req.evidence.get('status')} - {req.evidence.get('detail')}\n\n"
        f"Diff:\n{req.diff[:20000]}\n\n"
        "Does the diff stay in scope, address the goal, and match the worker's claim? "
        "Answer with APPROVE or REJECT as the very first word, then a short reason."
    )


class FakeReviewer(Reviewer):
    """Deterministic stand-in. Rejects an empty diff, a change to a file that is forbidden
    or not in the task's allowed files (naming the file), and a claim of success that the
    acceptance-check result contradicts. It does not look for secrets; the validators do.
    The validators still check scope on their own, whatever the reviewer says."""

    def __init__(self, name: str = "fake-reviewer", tier: str = "premium", family: str = "fake-family-b"):
        self.name, self.tier, self.family = name, tier, family

    def review(self, request: ReviewRequest) -> Verdict:
        if not request.diff.strip():
            return Verdict(False, "no change was made")
        touched = re.findall(r"^\+\+\+ b/(.+)$", request.diff, flags=re.MULTILINE)
        out_of_scope = check_scope(touched, request.task)
        if out_of_scope:
            return Verdict(False, f"out of scope: {out_of_scope[0]}")
        claims_success = any(w in request.claim.lower() for w in ("pass", "fixed", "done", "success"))
        if request.evidence.get("status") != "pass":
            if claims_success:
                return Verdict(False, f"claim of success contradicted by checks ({request.evidence.get('detail')})")
            return Verdict(False, f"acceptance checks do not pass ({request.evidence.get('detail')})")
        return Verdict(True, "claim matches check results")


class CommandReviewer(Reviewer):
    """Sends the review prompt to any command-line model tool and parses the answer."""

    def __init__(self, name: str, command, tier: str = "premium", family: str = "reviewer",
                 timeout: float = 300):
        self.name, self.command, self.tier, self.family, self.timeout = name, command, tier, family, timeout

    def review(self, request: ReviewRequest) -> Verdict:
        try:
            proc = run_command(self.command, Path("."), self.timeout, stdin_text=build_prompt(request))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReviewError(f"reviewer command failed: {exc}") from exc
        if proc.returncode != 0:
            raise ReviewError(f"reviewer command exited {proc.returncode}")
        return parse_verdict(proc.stdout)
