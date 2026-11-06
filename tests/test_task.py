import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sm_scan import task


@pytest.fixture
def root(tmp_path, monkeypatch):
    from sm_scan import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    return tmp_path


def _write_lock(root, pid, age_hours=0.0):
    d = root / "data"
    d.mkdir(exist_ok=True)
    ts = time.time() - age_hours * 3600
    (d / "last_run.lock").write_text(json.dumps({"pid": pid, "ts": ts}))
    return d / "last_run.lock"


def test_lock_clean_acquire(root):
    assert task.acquire_lock() is True


def test_lock_held_by_live_pid(root, monkeypatch):
    _write_lock(root, pid=4242)
    monkeypatch.setattr(task, "_pid_alive", lambda pid: True)
    assert task.acquire_lock() is False


def test_lock_stale_dead_pid_released(root, monkeypatch):
    _write_lock(root, pid=4242)
    monkeypatch.setattr(task, "_pid_alive", lambda pid: False)
    assert task.acquire_lock() is True


def test_lock_stale_too_old_released(root, monkeypatch):
    _write_lock(root, pid=4242, age_hours=7)
    monkeypatch.setattr(task, "_pid_alive", lambda pid: True)
    assert task.acquire_lock() is True


def test_register_selfcheck_failure_refused(root, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 1
            stderr = "ModuleNotFoundError"
            stdout = ""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = task.register()
    assert ok is False
    assert "ModuleNotFoundError" in msg or "import" in msg
    # 自检失败时不得调 schtasks
    assert all("schtasks" not in " ".join(str(x) for x in c) for c in calls)


def test_register_success_list_form_no_shell_escape(root, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["kw"] = kw

        class R:
            returncode = 0
            stdout = "SUCCESS"
            stderr = ""
        return R()

    import subprocess as sp

    monkeypatch.setattr(sp, "run", fake_run)
    ok, _ = task.register()
    assert ok is True
    cmd = seen["cmd"]
    assert isinstance(cmd, list)  # 列表形式、不经 shell
    assert cmd[0] == "schtasks"
    assert "/TN" in cmd and "sm-scan-daily" in cmd
    tr = cmd[cmd.index("/TR") + 1]
    assert '\\"' not in tr  # 无批处理转义残留
    assert seen["kw"].get("shell") is None  # 不经 shell


def test_cli_exit_code_passthrough(monkeypatch):
    from sm_scan import __main__ as main_mod

    monkeypatch.setattr(main_mod, "setup_logging", lambda: None)
    monkeypatch.setattr("sm_scan.engine.run_scan", lambda **kw: 3)
    assert main_mod.main(["scan"]) == 3
