"""数据目录绝对定位(spec §2):不依赖进程 cwd。

开发态 = 仓库根;打包态(sys.frozen)= exe 所在目录。
"""

import sys
from pathlib import Path

ROOT = (
    Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)


def data_dir() -> Path:
    d = ROOT / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = ROOT / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def presets_dir() -> "Path":
    """主题包目录定位(P2-4):
    打包版优先 exe 旁 presets/(用户可编辑),其次 _MEIPASS 打包内;开发态为仓库根。
    """
    if getattr(sys, "frozen", False):
        exe_side = Path(sys.executable).parent / "presets"
        if exe_side.is_dir():
            return exe_side
        m = getattr(sys, "_MEIPASS", None)
        if m and (Path(m) / "presets").is_dir():
            return Path(m) / "presets"
    return ROOT / "presets"
