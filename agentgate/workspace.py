"""Plain-filesystem change tracking: baseline copy, changed files, unified diff.

No version control is needed; the supervisor copies the workspace at start and
compares against that copy later.
"""
from __future__ import annotations

import difflib
import shutil
from pathlib import Path

_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".agentgate"}
_SKIP_SUFFIXES = {".pyc", ".pyo"}


def _files(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix in _SKIP_SUFFIXES:
            continue
        out[rel.as_posix()] = p
    return out


def take_baseline(workspace: Path, baseline: Path) -> None:
    if baseline.exists():
        shutil.rmtree(baseline)
    shutil.copytree(workspace, baseline, ignore=shutil.ignore_patterns(*_SKIP_DIRS, "*.pyc"))


def changed_files(baseline: Path, workspace: Path) -> list[str]:
    before, after = _files(baseline), _files(workspace)
    changed = []
    for rel in sorted(set(before) | set(after)):
        if rel not in before or rel not in after or before[rel].read_bytes() != after[rel].read_bytes():
            changed.append(rel)
    return changed


def unified_diff(baseline: Path, workspace: Path) -> str:
    before, after = _files(baseline), _files(workspace)
    chunks: list[str] = []
    for rel in changed_files(baseline, workspace):
        old = before[rel].read_text("utf-8", "replace").splitlines(keepends=True) if rel in before else []
        new = after[rel].read_text("utf-8", "replace").splitlines(keepends=True) if rel in after else []
        for line in difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}"):
            chunks.append(line if line.endswith("\n") else line + "\n")
    return "".join(chunks)
