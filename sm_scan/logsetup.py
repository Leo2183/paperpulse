"""滚动文件日志(CLI 与 GUI worker 共用,P2-14)。"""

import logging
from logging.handlers import RotatingFileHandler


def setup_logging() -> None:
    from . import config

    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return  # 已挂,不重复
    handler = RotatingFileHandler(
        config.data_dir() / "scan.log", maxBytes=1_000_000,
        backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
