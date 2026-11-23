import json
import shutil
from pathlib import Path

import pytest

from agentgate.adapters import FakeReviewer, FakeWorker, Verdict
from agentgate.demo import FIXED
from agentgate.routing import Router
from agentgate.supervisor import Supervisor

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def raw_task():
    raw = json.loads((EXAMPLES / "task.json").read_text("utf-8"))
    # A fast check instead of a nested pytest run, to keep the suite quick.
    raw["acceptance_checks"] = [
        '{python} -c "from discount import apply_discount as a; assert a(200, 25) == 150 and a(80, 0) == 80"'
    ]
    return raw


@pytest.fixture
def workspace(tmp_path):
    work = tmp_path / "work"
    shutil.copytree(EXAMPLES / "toy_project", work)
    return work


@pytest.fixture
def good_worker():
    return FakeWorker("cheap-worker", {"discount.py": FIXED}, "Fixed. Tests pass.")


class ApproveEverything(FakeReviewer):
    """A reviewer that always says APPROVE, to show the validators work without it."""

    def review(self, request):
        return Verdict(True, "approved without looking")


@pytest.fixture
def make_supervisor(tmp_path):
    def build(worker, reviewer=None):
        return Supervisor(tmp_path / "store", Router([worker], [reviewer or FakeReviewer()]))
    return build
