"""扫描引擎(spec §5):本模块先落纯函数;编排见 run_scan(Task 7 起)。"""

import hashlib
import re
from datetime import date, timedelta

_DOI_PREFIXES = (
    "https://doi.org/", "http://doi.org/", "doi.org/", "doi:", "info:doi/",
)


def normalize_doi(raw: str | None) -> str | None:
    """小写、剥常见前缀、分号拼接取首段;空 → None。"""
    if not raw or not raw.strip():
        return None
    s = raw.strip().lower().split(";")[0].strip()
    changed = True
    while changed:
        changed = False
        for p in _DOI_PREFIXES:
            if s.startswith(p):
                s = s[len(p):].strip()
                changed = True
    return s or None


def _norm_title(title: str) -> str:
    t = re.sub(r"[^\w\s]", "", title.lower())
    return " ".join(t.split())


def paper_key(doi: str | None, title: str) -> str:
    """归一化 DOI 优先;无 DOI 用标题规范化哈希兜底。"""
    d = normalize_doi(doi)
    if d:
        return d
    h = hashlib.sha1(_norm_title(title).encode("utf-8")).hexdigest()[:16]
    return f"titlehash:{h}"


def scan_window(
    last_ok_finished: date | None,
    today: date,
    backfill_days: int = 30,
    *,
    biorxiv: bool = False,
) -> tuple[date, date]:
    """增量窗口:锚定上次成功 run 结束日 -3 天;首跑回填。

    bioRxiv 首跑只回填 7 天(更早的预印本已由 Europe PMC SRC:PPR 覆盖)。
    窗口不会随轮次向前扩张(起点永远 = finished-3d 或 today-backfill)。
    """
    if last_ok_finished is None:
        days = 7 if biorxiv else backfill_days
        return (today - timedelta(days=days), today)
    start = last_ok_finished - timedelta(days=3)
    if start > today:
        start = today
    return (start, today)


def dates_ok(pub_date: str | None, window: tuple[date, date]) -> bool:
    """容缺日期:None/年月/年 → True(不因缺失过滤掉);完整日期须落窗内。"""
    if not pub_date:
        return True
    try:
        d = date.fromisoformat(pub_date)
    except ValueError:
        return True  # 只有年月/年,宽容放行
    return window[0] <= d <= window[1]
