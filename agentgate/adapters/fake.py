"""A deterministic worker for demos and tests: writes fixed files, returns a fixed claim."""
from __future__ import annotations

from pathlib import Path

from .base import WorkerAdapter


class FakeWorker(WorkerAdapter):
    def __init__(self, name: str, edits: dict[str, str | None], claim: str,
                 tier: str = "cheap", family: str = "fake-family-a"):
        """`edits` maps a relative path to new file text, or None to delete the file."""
        self.name = name
        self.edits = edits
        self.claim = claim
        self.tier = tier
        self.family = family
        self.calls = 0

    def dispatch(self, prompt: str, workdir: Path) -> str:
        self.calls += 1
        for rel, text in self.edits.items():
            target = Path(workdir) / rel
            if text is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        return f"{self.name}-{self.calls}"

    def poll(self, handle: str) -> str:
        return "done"

    def fetch_result(self, handle: str) -> str:
        return self.claim
