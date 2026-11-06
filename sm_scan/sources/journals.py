"""期刊订阅轨道核心(spec §3.2):双轨 fallback、BROAD 过滤、安全阀、回填。"""

import logging
from collections.abc import Iterator
from datetime import date, timedelta
from itertools import islice

from .. import queries, store
from . import crossref, europepmc
from .base import SourceError

log = logging.getLogger(__name__)

# 宽词表:词干(子串)+ 整词(\b 语义靠分词)+ 三组检索短语(spec §3.2)
BROAD_STEMS = [
    "metabolom", "metabolite", "mass spectrometry", "maldi",
    "natural product", "phytochem",
]
BROAD_WORDS = ["desi", "plant", "plants"]  # plant* 词干:宁宽勿漏,接受 transplantation
BROAD_PHRASES = [
    t.phrase for g in queries.DEFAULT_GROUPS for t in g.terms
]

MODE_FULL_THRESHOLD = 300  # 90 天 ≤300 → 建议 full(spec §3.2)
SAFETY_VALVE = 30  # 单刊单日 >30 → 降级 filter;仅日常窗口(≤7 天)
DAILY_WINDOW_DAYS = 7
BACKFILL_DAYS = 90

# 32 本默认清单(v5 分档:filter 19 / full 13;IJMS 降 filter 保留)
DEFAULT_JOURNAL_ROWS = [
    # filter(高发文量)
    dict(name="International Journal of Molecular Sciences", issn="1422-0067", mode="filter"),
    dict(name="Analytical Chemistry", issn="0003-2700", mode="filter"),
    dict(name="Talanta", issn="0039-9140", mode="filter"),
    dict(name="Food Chemistry", issn="0308-8146", mode="filter"),
    dict(name="Metabolites", issn="2218-1989", mode="filter"),
    dict(name="Analytica Chimica Acta", issn="0003-2670", mode="filter"),
    dict(name="Analytical and Bioanalytical Chemistry", issn="1618-2642", mode="filter"),
    # filter(巨刊)
    dict(name="Nature", issn="0028-0836", mode="filter"),
    dict(name="Science", issn="0036-8075", mode="filter"),
    dict(name="Cell", issn="0092-8674", mode="filter"),
    dict(name="Proceedings of the National Academy of Sciences", issn="0027-8424", mode="filter"),
    dict(name="Nature Methods", issn="1548-7091", mode="filter"),
    dict(name="Nature Biotechnology", issn="1087-0156", mode="filter"),
    dict(name="Nature Chemistry", issn="1755-4349", mode="filter"),
    dict(name="Nature Plants", issn="2055-0278", mode="filter"),
    dict(name="Nature Communications", issn="2041-1723", mode="filter"),
    dict(name="Science Advances", issn="2375-2548", mode="filter"),
    dict(name="Advanced Science", issn="2198-3844", mode="filter"),
    dict(name="Angewandte Chemie International Edition", issn="1433-7851", mode="filter"),
    # full(窄刊,≤300/90 天)
    dict(name="Journal of the American Society for Mass Spectrometry", issn="1044-0305", mode="full"),
    dict(name="Journal of Mass Spectrometry", issn="1076-5174", mode="full"),
    dict(name="Rapid Communications in Mass Spectrometry", issn="0951-4198", mode="full"),
    dict(name="Mass Spectrometry Reviews", issn="0277-7037", mode="full"),
    dict(name="International Journal of Mass Spectrometry", issn="1387-3806", mode="full"),
    dict(name="TrAC Trends in Analytical Chemistry", issn="0165-9936", mode="full"),
    dict(name="Phytochemistry", issn="0031-9422", mode="full"),
    dict(name="Phytochemical Analysis", issn="0958-0344", mode="full"),
    dict(name="Journal of Natural Products", issn="0163-3864", mode="full"),
    dict(name="Natural Product Reports", issn="0265-0568", mode="full"),
    dict(name="Trends in Plant Science", issn="1360-1385", mode="full"),
    dict(name="Horticulture Research", issn="2052-7276", mode="full"),
    dict(name="Food Chemistry: X", issn="2590-1575", mode="full"),
]


def suggest_mode(count_90d: int) -> str:
    """按实测 90 天发文量自动建议模式(≤300 → full)。"""
    return "full" if count_90d <= MODE_FULL_THRESHOLD else "filter"


def _broad_hit(title: str, abstract: str | None) -> bool:
    """宽词表匹配:标题(+有摘要时摘要);无摘要仅标题(spec §3.2 三态)。"""
    text = (title + "\n" + abstract) if abstract else title
    low = text.lower()
    if any(s in low for s in BROAD_STEMS):
        return True
    if any(s.lower() in low for s in BROAD_PHRASES):
        return True
    words = set(low.split())
    return bool(words & set(BROAD_WORDS))


def _probe(row: dict, window: tuple[date, date]):
    """运行时 fallback:Europe PMC 优先,0 条转 Crossref;双 0 返回 None。"""
    try:
        q = (
            f'ISSN:"{row["issn"]}" AND FIRST_IDATE:'
            f"[{window[0].isoformat()} TO {window[1].isoformat()}]"
        )
        it = europepmc.search(q, *window)
        first = next(it, None)
        if first is not None:

            def gen():
                yield first
                yield from it

            return "europepmc", gen()
    except SourceError as e:
        log.warning("journal %s europepmc probe failed: %s", row["name"], e)
    try:
        it = crossref.fetch_journal(row["issn"], *window)
        first = next(it, None)
        if first is not None:

            def gen2():
                yield first
                yield from it

            return "crossref", gen2()
    except SourceError as e:
        log.warning("journal %s crossref fallback failed: %s", row["name"], e)
    return "", None


def _cache_route(conn, jid: int, route: str) -> None:
    conn.execute(
        "UPDATE journal_subscriptions SET route=? WHERE id=?", (route, jid))
    conn.commit()


def route(conn, jid: int, window: tuple[date, date]) -> str:
    """探测并缓存单刊路由;双 0 不缓存(下轮重试)。"""
    row = dict(conn.execute(
        "SELECT * FROM journal_subscriptions WHERE id=?", (jid,)).fetchone())
    kind, _ = _probe(row, window)
    if kind:
        _cache_route(conn, jid, kind)
    return kind


def _scan_one(
    conn, row: dict, window: tuple[date, date], today: date
) -> Iterator[tuple[store.Paper, dict]]:
    kind, papers = _probe(row, window)
    if not kind:
        log.warning("journal %s: no results on either route; retry next run",
                    row["name"])
        return
    _cache_route(conn, row["id"], kind)

    daily = (window[1] - window[0]).days <= DAILY_WINDOW_DAYS
    demoted = False
    if row["mode"] == "full":
        batch = list(papers)
        if daily and len(batch) > SAFETY_VALVE:
            log.warning(
                "journal %s: %d new items (> %d) on a daily window -- "
                "demoted to filter for this run", row["name"], len(batch),
                SAFETY_VALVE)
            demoted = True
            batch = [p for p in batch if _broad_hit(p["title"], p["abstract"])]
        for p in batch:
            yield p, dict(row, _demoted=demoted)
        return
    # filter 刊(无摘要刊自动仅标题)
    for p in papers:
        if _broad_hit(p["title"], p["abstract"]):
            yield p, row


def scan_journals(
    conn, window: tuple[date, date], today: date | None = None,
    mailto: str | None = None,
) -> Iterator[tuple[store.Paper, dict]]:
    today = today or date.today()
    if not conn.execute("SELECT COUNT(*) FROM journal_subscriptions").fetchone()[0]:
        store.seed_journals(conn, DEFAULT_JOURNAL_ROWS)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM journal_subscriptions WHERE enabled=1 ORDER BY id")]
    for row in rows:
        yield from _scan_one(conn, row, window, today)


def backfill_journal(conn, jid: int, today: date | None = None) -> int:
    """GUI 新增刊自动回填近 90 天(QThread 逐页调用,窗口 >7 天安全阀自动跳过)。"""
    today = today or date.today()
    row = dict(conn.execute(
        "SELECT * FROM journal_subscriptions WHERE id=?", (jid,)).fetchone())
    window = (today - timedelta(days=BACKFILL_DAYS), today)
    n = 0
    for p, _ in _scan_one(conn, row, window, today):
        if store.upsert_paper(conn, p):
            n += 1
    return n
