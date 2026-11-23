"""A generic worker that shells out to any command-line model tool.

You configure the command; this code contains no vendor names, keys or endpoints.
The prompt is written to the command's standard input. The command runs inside the
workspace and edits files there. Whatever it prints is taken as its summary.
Any credentials the tool needs are its own business (its config or environment).

Example (the command is yours to choose):
    CommandWorker("my-tool", ["my-model-cli", "--non-interactive"], family="vendor-a")
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..validators import run_command
from .base import BlockedError, WorkerAdapter


class CommandWorker(WorkerAdapter):
    def __init__(self, name: str, command, tier: str = "cheap", family: str = "worker",
                 timeout: float = 600):
        self.name = name
        self.command = command          # a string (split like a shell would) or a list
        self.tier = tier
        self.family = family
        self.timeout = timeout
        self._results: dict[str, str] = {}

    def dispatch(self, prompt: str, workdir: Path) -> str:
        try:
            proc = run_command(self.command, Path(workdir), self.timeout, stdin_text=prompt)
        except FileNotFoundError as exc:
            raise BlockedError(f"command not found: {exc.filename}") from exc
        except subprocess.TimeoutExpired as exc:
            raise BlockedError(f"command timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            raise BlockedError(f"command exited {proc.returncode}: {proc.stderr.strip()[:200]}")
        handle = f"{self.name}-{len(self._results) + 1}"
        self._results[handle] = proc.stdout.strip()
        return handle

    def poll(self, handle: str) -> str:
        return "done"  # the command runs to completion inside dispatch()

    def fetch_result(self, handle: str) -> str:
        return self._results.get(handle, "")
