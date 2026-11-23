"""The worker interface. Every model or tool plugs in by implementing three calls."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BlockedError(Exception):
    """The worker could not start or got stuck. The task is moved to 'blocked'."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class WorkerAdapter(ABC):
    name = "base"
    tier = "cheap"       # which cost tier this worker belongs to
    family = "unknown"   # model family; the reviewer must come from a different one

    @abstractmethod
    def dispatch(self, prompt: str, workdir: Path) -> str:
        """Start work on `prompt` inside `workdir`. Return an opaque handle or raise BlockedError."""

    @abstractmethod
    def poll(self, handle: str) -> str:
        """Return 'running' or 'done'. Raise BlockedError if stuck."""

    @abstractmethod
    def fetch_result(self, handle: str) -> str:
        """Return the worker's own summary of what it did. This is a claim, not proof."""
