"""Windows 任务计划集成 + 防重入锁(spec §10)。

- 注册前自检 import(pythonw 无控制台,静默失败最难排查)
- schtasks 一律 subprocess 列表形式、不经 shell(/TR 的 \" 是批处理语法)
- sys.frozen 双形态:打包版注册 sm-scan.exe,开发态 pythonw -m sm_scan
"""

import ctypes
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

TASK_NAME = "sm-scan-daily"
LOCK_STALE_HOURS = 6


def _pid_alive(pid: int) -> bool:
    """OpenProcess 探测(不用 os.kill——Windows 下语义不同,危险)。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    kernel32.CloseHandle(h)
    return True


def acquire_lock() -> bool:
    """data/last_run.lock(pid+ts):已持锁 → False;stale(pid 死/>6h)→ 覆盖 True。"""
    lock = config.data_dir() / "last_run.lock"
    if lock.exists():
        try:
            info = json.loads(lock.read_text(encoding="utf-8"))
            age_h = (time.time() - info.get("ts", 0)) / 3600
            if _pid_alive(info.get("pid", -1)) and age_h < LOCK_STALE_HOURS:
                log.info("another scan is running (pid %s); exiting", info.get("pid"))
                return False
            log.warning("stale lock (pid=%s alive=%s age=%.1fh); taking over",
                        info.get("pid"), _pid_alive(info.get("pid", -1)), age_h)
        except (ValueError, OSError):
            log.warning("corrupt lock file; taking over")
    lock.write_text(json.dumps({"pid": _my_pid(), "ts": time.time()}))
    return True


def _my_pid() -> int:
    import os

    return os.getpid()


def _scan_command() -> str:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).with_name("sm-scan.exe")
        return f'"{exe}" scan'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" -m sm_scan scan'


def _selfcheck() -> tuple[bool, str]:
    """开发态前置 pip install -e . 校验:import sm_scan 失败则拒绝注册。"""
    if getattr(sys, "frozen", False):
        return True, ""
    python = sys.executable
    r = subprocess.run(
        [python, "-c", "import sm_scan"], capture_output=True, text=True
    )
    if r.returncode != 0:
        return False, (r.stderr or "import sm_scan failed").strip()[:300]
    return True, ""


def register() -> tuple[bool, str]:
    ok, err = _selfcheck()
    if not ok:
        return False, f"自检失败(先 pip install -e .):{err}"
    cmd = [
        "schtasks", "/Create", "/TN", TASK_NAME,
        "/TR", _scan_command(),
        "/SC", "DAILY", "/ST", "09:00", "/F",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "schtasks failed").strip()[:300]
    return True, "已注册每日 09:00(仅用户登录时运行;空档由窗口自动覆盖)"


def remove() -> tuple[bool, str]:
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True, text=True, errors="replace",
    )
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[:300]
    return True, "已删除"


def status() -> dict:
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True, text=True, errors="replace",
    )
    return {"registered": r.returncode == 0,
            "raw": (r.stdout or r.stderr or "")[:500]}
