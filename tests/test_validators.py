from agentgate import validators, workspace as ws
from agentgate.schema import Task


def task_from(raw):
    return Task.from_raw(raw, "T-0001")


def test_workspace_tracks_changes_and_ignores_caches(tmp_path):
    base, work = tmp_path / "base", tmp_path / "work"
    for d in (base, work):
        d.mkdir()
        (d / "a.txt").write_text("one\n")
    (work / "__pycache__").mkdir()
    (work / "__pycache__" / "x.pyc").write_bytes(b"0")
    assert ws.changed_files(base, work) == []
    (work / "a.txt").write_text("two\n")
    (work / "new.txt").write_text("x\n")
    assert ws.changed_files(base, work) == ["a.txt", "new.txt"]
    assert "+two" in ws.unified_diff(base, work)


def test_scope_check(raw_task, workspace, tmp_path):
    base = tmp_path / "base"
    ws.take_baseline(workspace, base)
    task = task_from(raw_task)
    (workspace / "discount.py").write_text("# changed\n")
    assert validators.scope_check(task, base, workspace)["status"] == "pass"
    (workspace / "test_discount.py").write_text("# changed\n")
    result = validators.scope_check(task, base, workspace)
    assert result["status"] == "fail" and "test_discount.py" in result["detail"]


def test_tests_pass_fail_and_error(raw_task, workspace):
    task = task_from(raw_task)
    assert validators.tests_pass(task, workspace)["status"] == "fail"   # the toy bug
    broken = task_from({**raw_task, "acceptance_checks": ["no-such-command-xyz"]})
    assert validators.tests_pass(broken, workspace)["status"] == "error"


def test_no_secret_flags_added_secret_without_echoing_it():
    fake_value = "abcd1234" * 3
    diff = f"+++ b/config.py\n+token = \"{fake_value}\"\n"
    result = validators.no_secret(diff)
    assert result["status"] == "fail"
    assert "config.py" in result["detail"] and fake_value not in result["detail"]


def test_no_secret_flags_key_block_and_ignores_removed_lines():
    header = "-----BEGIN " + "RSA PRIVATE KEY-----"
    assert validators.no_secret(f"+++ b/k.txt\n+{header}\n")["status"] == "fail"
    assert validators.no_secret(f"+++ b/k.txt\n-{header}\n")["status"] == "pass"


def test_rollup_order():
    p, f, e = ({"status": s} for s in ("pass", "fail", "error"))
    assert validators.rollup([p, p]) == "pass"
    assert validators.rollup([p, f]) == "fail"
    assert validators.rollup([p, f, e]) == "error"
