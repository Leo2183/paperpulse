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
