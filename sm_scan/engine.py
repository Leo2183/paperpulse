"""扫描引擎(spec §5):纯函数 + 三轨编排 run_scan。"""

import hashlib
import json
import logging
import re
import time
from datetime import date, timedelta

from . import queries, store, tags
from .sources import biorxiv, europepmc, journals
from .sources.base import SourceError

log = logging.getLogger(__name__)

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


# ---------------------------------------------------------------- 三轨编排

def last_ok_finished(conn) -> date | None:
    row = conn.execute(
        "SELECT finished_at FROM runs WHERE status IN ('ok','partial')"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or not row["finished_at"]:
        return None
    return date.fromisoformat(row["finished_at"][:10])


def run_scan(conn=None, *, dry_run: bool = False, llm: bool = False,
             days: int | None = None) -> int:
    """三轨扫描(本任务实现主题轨道;订阅/bioRxiv 在 Task 11 接入)。

    退出码:0=ok,2=partial,3=failed(spec §11)。
    """
    own = conn is None
    if not dry_run:
        from . import task as task_mod

        if not task_mod.acquire_lock():
            log.warning("lock held; scan skipped")
            return 0
    conn = conn or store.connect()
    store.init_db(conn)
    if not conn.execute("SELECT COUNT(*) FROM term_groups").fetchone()[0]:
        store.seed_groups(conn, [
            dict(name=g.name, enabled=1,
                 terms=[{"phrase": t.phrase, "scope": t.scope} for t in g.terms],
                 refine=[{"phrase": t.phrase, "scope": t.scope} for t in g.refine])
            for g in queries.DEFAULT_GROUPS
        ])
    settings = store.load_settings(conn)
    groups = [queries.from_row(r) for r in store.load_groups(conn)]
    groups = [g for g in groups if g.enabled]
    g3 = next((g for g in groups if "注释" in g.name), None)
    g3_phrases = [t.phrase for t in g3.terms] if g3 else []

    window = scan_window(
        last_ok_finished(conn), date.today(),
        backfill_days=int(days or settings.get("backfill_days", "30")),
    )

    if dry_run:
        for g in groups:
            print(f"[dry-run] {g.name}: {queries.to_europepmc_query(g, *window)}")
        n_j = conn.execute(
            "SELECT COUNT(*) FROM journal_subscriptions WHERE enabled=1"
        ).fetchone()[0]
        print(f"[dry-run] journals: {n_j} 刊订阅,窗口 {window[0]} → {window[1]}(路由运行时探测)")
        bw = scan_window(
            last_ok_finished(conn), date.today(),
            backfill_days=int(days or settings.get("backfill_days", "30")),
            biorxiv=True,
        )
        print(f"[dry-run] biorxiv: 窗口 {bw[0]} → {bw[1]}(首跑回填 7 天,此后随主窗口)")
        print(f"[dry-run] window: {window[0]} → {window[1]}")
        return 0

    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.execute(
        "INSERT INTO runs (started_at, status) VALUES (?, 'running')", (started,)
    )
    run_id = cur.lastrowid
    new_count = 0
    errors: list[str] = []
    per_source: dict = {}
    failed_tracks: set[str] = set()
    enabled_tracks: set[str] = set()

    def _track(name: str) -> dict:
        d = per_source.setdefault(name, {"fetched": 0, "new": 0})
        enabled_tracks.add(name)
        return d

    # --- 轨道1:Europe PMC 主题检索 ---
    if settings.get("src_europepmc_enabled", "1") == "1":
        t = _track("europepmc")
        any_ok = False
        for g in groups:
            try:
                for p in europepmc.search(
                    queries.to_europepmc_query(g, *window), *window
                ):
                    t["fetched"] += 1
                    p["tags"] = tags.assign(p["title"], p["abstract"], g3_phrases)
                    p["matched_groups"] = [g.name]
                    if store.upsert_paper(conn, p):
                        new_count += 1
                        t["new"] += 1
                any_ok = True
            except SourceError as e:
                log.error("topic track group %s failed: %s", g.name, e)
                errors.append(f"{g.name}: {e}")
        if groups and not any_ok:
            failed_tracks.add("europepmc")
    else:
        per_source["europepmc"] = {"disabled": True}

    # --- 轨道2:期刊订阅 ---
    if settings.get("src_journals_enabled", "1") == "1":
        t = _track("journals")
        try:
            for p, jrow in journals.scan_journals(
                conn, window, date.today(),
                mailto=settings.get("crossref_mailto") or None,
            ):
                t["fetched"] += 1
                stale = p.pop("_stale", False)  # 注册日保护:只入库不计新增
                p["subscription"] = jrow["name"]
                p["tags"] = tags.assign(p["title"], p["abstract"], g3_phrases)
                if store.upsert_paper(conn, p) and not stale:
                    new_count += 1
                    t["new"] += 1
        except SourceError as e:
            log.error("journal track failed: %s", e)
            errors.append(f"journals: {e}")
            failed_tracks.add("journals")
    else:
        per_source["journals"] = {"disabled": True}

    # --- 轨道3:bioRxiv ---
    if settings.get("src_biorxiv_enabled", "1") == "1":
        t = _track("biorxiv")
        bw = scan_window(
            last_ok_finished(conn), date.today(),
            backfill_days=int(days or settings.get("backfill_days", "30")),
            biorxiv=True,
        )
        try:
            for p in biorxiv.scan(bw[0], bw[1], groups):
                t["fetched"] += 1
                p["tags"] = tags.assign(p["title"], p["abstract"], g3_phrases)
                if store.upsert_paper(conn, p):
                    new_count += 1
                    t["new"] += 1
        except SourceError as e:
            log.error("biorxiv track failed: %s", e)
            errors.append(f"biorxiv: {e}")
            failed_tracks.add("biorxiv")
    else:
        per_source["biorxiv"] = {"disabled": True}

    if llm:
        log.warning("--llm requested but LLM track lands in a later task; skipped")

    if enabled_tracks and failed_tracks == enabled_tracks:
        status = "failed"  # 全部启用轨道失败
    elif failed_tracks:
        status = "partial"
    else:
        status = "ok"
    finished = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, new_count=?, per_source=?, error=?"
        " WHERE id=?",
        (finished, status, new_count,
         json.dumps(per_source, ensure_ascii=False),
         "; ".join(errors) or None, run_id),
    )
    conn.commit()
    if own:
        conn.close()
    return {"ok": 0, "partial": 2, "failed": 3}[status]
