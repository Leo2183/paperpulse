import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from paperpulse import task


@pytest.fixture
def root(tmp_path, monkeypatch):
    from paperpulse import config

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
    assert "/TN" in cmd and "paperpulse-daily" in cmd
    tr = cmd[cmd.index("/TR") + 1]
    assert '\\"' not in tr  # 无批处理转义残留
    assert seen["kw"].get("shell") is None  # 不经 shell


def test_cli_exit_code_passthrough(monkeypatch):
    from paperpulse import __main__ as main_mod

    monkeypatch.setattr(main_mod, "setup_logging", lambda: None)
    monkeypatch.setattr("paperpulse.engine.run_scan", lambda **kw: 3)
    assert main_mod.main(["scan"]) == 3


def test_scan_command_frozen_points_to_cli_dir(monkeypatch):
    """P1-5:打包态 GUI 注册的任务必须指向真实存在的 CLI exe。"""
    import sys as _sys
    from pathlib import Path

    fake_gui = Path("D:/fake/dist/paperpulse-gui/paperpulse-gui.exe")
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_sys, "executable", str(fake_gui))
    cmd = task._scan_command()
    assert "paperpulse-gui" not in cmd
    assert "paperpulse" in cmd


def test_register_refuses_when_target_missing(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr(task, "_selfcheck", lambda: (True, ""))
    monkeypatch.setattr(task, "_scan_command",
                        lambda: r'"D:\no\such\paperpulse.exe" scan')

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(sp, "run", fake_run)
    seen = []
    monkeypatch.setattr(sp, "run",
                        lambda cmd, **kw: seen.append(cmd) or fake_run(cmd, **kw))
    ok, msg = task.register()
    assert ok is False
    assert not seen  # 目标不存在时不得调 schtasks


def test_register_command_flags_appear_once(monkeypatch):
    """防回归:cmd 不得重复插入 flag(P1-5 补丁曾双份,schtasks 拒绝)。"""
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    import subprocess as sp

    monkeypatch.setattr(task, "_selfcheck", lambda: (True, ""))
    import sys
    monkeypatch.setattr(task, "_scan_command",
                        lambda: f'"{sys.executable}" -m paperpulse scan')
    monkeypatch.setattr(sp, "run", fake_run)
    ok, _ = task.register()
    assert ok is True
    cmd = seen["cmd"]
    assert cmd.count("/Create") == 1 and cmd.count("/TR") == 1
