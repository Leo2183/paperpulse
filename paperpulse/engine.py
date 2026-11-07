"""扫描引擎(spec §5):纯函数 + 三轨编排 run_scan。"""

import hashlib
import json
import logging
import re
import time
from datetime import date, timedelta

from . import prepare, queries, store, tags
from .ids import normalize_doi, paper_key  # noqa: F401  (re-export)
from .sources import biorxiv, europepmc, journals
from .sources.base import SourceError

log = logging.getLogger(__name__)



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




# ---------------------------------------------------------------- 三轨编排

def last_ok_finished(conn) -> date | None:
    row = conn.execute(
        "SELECT finished_at FROM runs WHERE status = 'ok'"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or not row["finished_at"]:
        return None
    return date.fromisoformat(row["finished_at"][:10])


def run_scan(conn=None, *, dry_run: bool = False, llm: bool = False,
             days: int | None = None, run_ts: str | None = None) -> int:
    """三轨扫描。run_ts:本次 run 的起始时间戳(默认墙钟)——所有入库论文
    的 first_seen 统一取该值,跨零点 run 归入起始日(spec §5)。

    退出码:0=ok,2=partial,3=failed(spec §11)。
    """
    own = conn is None
    if not dry_run:
        from . import task as task_mod

        if not task_mod.acquire_lock():
            log.warning("lock held; scan skipped")
            return 0
    try:
        conn = conn or store.connect()
        store.init_db(conn)
        if not conn.execute("SELECT COUNT(*) FROM term_groups").fetchone()[0]:
            store.seed_groups(conn, [
                dict(id=g.id, name=g.name, enabled=1,
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
        if days:  # P2-5:显式 --days 时扩大窗口(起点取更早者)
            forced = date.today() - timedelta(days=days)
            window = (min(window[0], forced), window[1])

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

        started = run_ts or time.strftime("%Y-%m-%dT%H:%M:%S")
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

        def _upsert(p, *, group_names=None, subscription=None) -> bool:
            prepare.prepare_paper(
                p, group_names=group_names or [], g3_phrases=g3_phrases,
                started=started, subscription=subscription)
            return store.upsert_paper(conn, p)

        # --- 轨道1:Europe PMC 主题检索 ---
        if settings.get("src_europepmc_enabled", "1") == "1":
            t = _track("europepmc")
            for g in groups:
                try:
                    for p in europepmc.search(
                        queries.to_europepmc_query(g, *window), *window
                    ):
                        t["fetched"] += 1
                        if _upsert(p, group_names=[str(g.id)]):
                            new_count += 1
                            t["new"] += 1
                except SourceError as e:
                    log.error("topic track group %s failed: %s", g.name, e)
                    errors.append(f"{g.name}: {e}")
            if groups and errors:
                # 任一组失败 → 轨道受损(partial);全部轨道失败才 failed(spec §13)
                failed_tracks.add("europepmc")
        else:
            per_source["europepmc"] = {"disabled": True}

        # --- 轨道2:期刊订阅 ---
        if settings.get("src_journals_enabled", "1") == "1":
            t = _track("journals")
            try:
                demoted: set[str] = set()
                for p, jrow in journals.scan_journals(
                    conn, window, date.today(),
                    mailto=settings.get("crossref_mailto") or None,
                ):
                    t["fetched"] += 1
                    stale = p.get("_stale", False)  # 注册日保护:只入库不计新增
                    if jrow.get("_demoted"):
                        demoted.add(jrow["name"])  # P2-2:日报可见
                    if _upsert(p, subscription=jrow["name"]) and not stale:
                        new_count += 1
                        t["new"] += 1
                if demoted:
                    t["demoted"] = sorted(demoted)
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
                    if _upsert(p):  # group_names=None:保留 scan 给出的组命中
                        new_count += 1
                        t["new"] += 1
            except SourceError as e:
                log.error("biorxiv track failed: %s", e)
                errors.append(f"biorxiv: {e}")
                failed_tracks.add("biorxiv")
        else:
            per_source["biorxiv"] = {"disabled": True}

        if llm:
            _run_llm_digest(conn, settings, started)

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
    finally:
        if not dry_run:
            from . import task as _tm
            _tm.release_lock()



def _run_llm_digest(conn, settings: dict, started: str) -> None:
    """--llm 路径(spec §8):对本次 run 新增的未读论文批量导读。"""
    from . import llm as llm_mod

    if not (settings.get("llm_base_url") and settings.get("llm_model")):
        log.warning("--llm set but LLM not configured (base_url/model); digest skipped")
        print("[llm] 未配置:请在 GUI「检索式配置 → 源与 LLM」填写 base_url 与 model;"
              "本次跳过导读(spec §13 明确报错,不静默)")
        return
    key = ""
    if settings.get("llm_api_key"):
        try:
            key = llm_mod.dpapi_decrypt(settings["llm_api_key"])
        except OSError:
            log.warning("llm_api_key decrypt failed; digest proceeds without auth")
    cfg = dict(llm_base_url=settings["llm_base_url"],
               llm_model=settings["llm_model"], llm_api_key=key)
    papers = [dict(r) for r in conn.execute(
        "SELECT id, title, abstract, llm_digest FROM papers"
        " WHERE status='unread' AND llm_digest IS NULL AND abstract IS NOT NULL"
        " AND substr(first_seen,1,10)=substr(?,1,10)", (started,))]
    if not papers:
        return
    ok, fail = llm_mod.batch_digest(conn, papers, cfg)
    log.info("LLM digest: %d ok, %d failed/skipped", ok, fail)


def last_report_date(conn) -> "date":
    """最新已结束 run 的起始日期(日报文件名应取它,spec §5)。"""
    row = conn.execute(
        "SELECT started_at FROM runs WHERE finished_at IS NOT NULL"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return date.fromisoformat(row["started_at"][:10]) if row else date.today()
