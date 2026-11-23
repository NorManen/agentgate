import sys

import pytest

from agentgate import cli, demo


def test_demo_runs_offline_and_shows_four_outcomes(capsys):
    assert demo.main() == 0
    out = capsys.readouterr().out
    assert "run 1: done, run 2: rejected, run 3: rejected, run 4: validation_failed" in out
    assert "REJECT: out of scope: test_discount.py is forbidden" in out
    assert "secrets=fail" in out and "k7k7" not in out
    assert len(out.splitlines()) < 35


def test_cli_demo_command(monkeypatch):
    monkeypatch.setattr(demo, "main", lambda: 0)   # the demo itself is tested above
    with pytest.raises(SystemExit) as exc:
        cli.main(["demo"])
    assert exc.value.code == 0


def test_cli_create_and_status(tmp_path, capsys):
    store = str(tmp_path / "s")
    task_file = demo.EXAMPLES / "task.json"
    with pytest.raises(SystemExit) as exc:
        cli.main(["--store", store, "create", str(task_file)])
    assert exc.value.code == 0
    with pytest.raises(SystemExit):
        cli.main(["--store", store, "status"])
    assert "T-0001" in capsys.readouterr().out


def test_cli_run_with_command_models(tmp_path, monkeypatch, capsys):
    """A real-model wiring test using tiny scripts as the 'models'."""
    import shutil
    work = tmp_path / "work"
    shutil.copytree(demo.EXAMPLES / "toy_project", work)
    worker = tmp_path / "worker.py"
    worker.write_text(f"open('discount.py', 'w').write({demo.FIXED!r})\nprint('fixed it')\n")
    reviewer = tmp_path / "reviewer.py"
    reviewer.write_text("print('APPROVE ok')\n")
    monkeypatch.setenv("AGENTGATE_WORKER_CMD", f'"{sys.executable}" "{worker.as_posix()}"')
    monkeypatch.setenv("AGENTGATE_REVIEWER_CMD", f'"{sys.executable}" "{reviewer.as_posix()}"')
    monkeypatch.setenv("AGENTGATE_WORKER_FAMILY", "a")
    monkeypatch.setenv("AGENTGATE_REVIEWER_FAMILY", "b")
    with pytest.raises(SystemExit) as exc:
        cli.main(["--store", str(tmp_path / "s"), "run", str(demo.EXAMPLES / "task.json"),
                  "--workspace", str(work)])
    assert exc.value.code == 0, capsys.readouterr()
