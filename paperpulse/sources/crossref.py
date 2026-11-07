"""Crossref 适配器(spec §3.2):订阅拉取(fallback 路由)+ 刊名解析。

已知限制(第 04 轮实测):Elsevier 不提交 abstract → 无摘要条目置 None,
filter 降级仅标题匹配(由 journals.py 处理);目录页伪装 journal-article →
标题黑名单必过;注册日 ≠ 出版日 → issued 填 pub_date + 12 个月保护。
"""

import html
import logging
import re
from collections.abc import Iterator
from datetime import date, timedelta

from .. import ids, store
from .base import http_get

log = logging.getLogger(__name__)

API = "https://api.crossref.org/works"
JOURNALS_API = "https://api.crossref.org/journals"

TITLE_BLACKLIST = [
    "contents continued", "editorial board", "graphical abstract",
    "in this issue", "cover", "erratum", "corrigendum", "publisher's note",
]
_BLACKLIST_EXACT = {"cover", "front matter", "back matter", "table of contents"}
_BLACKLIST_PREFIX = (
    "graphical abstract", "contents continued", "editorial board",
    "in this issue", "publisher's note", "erratum", "corrigendum",
    "front matter",
)


def _blacklisted(title: str) -> bool:
    """精确判定:全等或已知目录页前缀;子串(discovery/coverage)不误杀。"""
    t = title.strip().lower()
    return t in _BLACKLIST_EXACT or t.startswith(_BLACKLIST_PREFIX)


def _strip_jats(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _issued_date(item: dict) -> str | None:
    parts = (item.get("issued") or {}).get("date-parts") or []
    if not parts or not parts[0]:
        return None
    y = parts[0][0]
    m = parts[0][1] if len(parts[0]) > 1 else 1
    d = parts[0][2] if len(parts[0]) > 2 else 1
    return f"{y:04d}-{m:02d}-{d:02d}"


def _authors_display(item: dict) -> str | None:
    authors = item.get("author") or []
    if not authors:
        return None
    first = authors[0]
    name = " ".join(
        x for x in (first.get("given"), first.get("family")) if x
    ).strip()
    return f"{name} et al." if len(authors) > 1 else name


def _to_paper(item: dict, today: date) -> store.Paper:
    doi = ids.normalize_doi(item.get("DOI"))
    title = (item.get("title") or [""])[0].strip()
    pub = _issued_date(item)
    p = store.Paper(
        id=ids.paper_key(doi, title),
        doi=doi,
        pmcid=None,
        title=title,
        authors=_authors_display(item),
        journal=(item.get("container-title") or [None])[0],
        pub_date=pub,
        indexed_date=((item.get("created") or {}).get("date-time") or "")[:10] or None,
        source="crossref",
        sources=["crossref"],
        subscription=None,
        url=f"https://doi.org/{doi}" if doi else item.get("URL"),
        abstract=_strip_jats(item["abstract"]) if item.get("abstract") else None,
        tags=[],
        matched_groups=[],
        status="unread",
        starred=0,
        to_read=0,
        llm_digest=None,
        first_seen=None,
        updated_at=None,
    )
    if pub:
        try:
            if date.fromisoformat(pub) < today - timedelta(days=365):
                p["_stale"] = True  # type: ignore[typeddict-unknown-key]
        except ValueError:
            pass
    return p


def fetch_journal(
    issn: str, d_from: date, d_to: date, mailto: str | None = None,
    today: date | None = None,
) -> Iterator[store.Paper]:
    """按 ISSN + DOI 注册日窗口拉取;黑名单/陈年标记在产出层处理。"""
    today = today or date.today()
    cursor = "*"
    while True:
        body = http_get(API, params={
            "filter": (
                f"issn:{issn},from-created-date:{d_from.isoformat()},"
                f"until-created-date:{d_to.isoformat()}"
            ),
            "rows": "100",
            "cursor": cursor,
        }, mailto=mailto)
        msg = body.get("message", {})
        items = msg.get("items", [])
        for it in items:
            title = ((it.get("title") or [""])[0] or "").strip()
            if _blacklisted(title):
                continue
            try:
                yield _to_paper(it, today)
            except Exception:
                log.exception("bad Crossref record: %r", it.get("DOI"))
        nxt = msg.get("next-cursor")
        if not items or not nxt or nxt == cursor:
            return
        cursor = nxt


def resolve_journal(name: str, mailto: str | None = None) -> list[tuple[str, str]]:
    """刊名 → [(title, issn)];精确 title 相等优先(防 'Nature' 前缀歧义)。"""
    body = http_get(JOURNALS_API, params={"query": name}, mailto=mailto)
    items = body.get("message", {}).get("items", [])
    out = []
    for it in items:
        issns = it.get("ISSN") or []
        if it.get("title") and issns:
            out.append((it["title"], issns[0]))
    out.sort(key=lambda t: (t[0].strip().lower() != name.strip().lower(),))
    return out


def count_recent(issn: str, days: int = 90, mailto: str | None = None) -> int:
    d_from = (date.today() - timedelta(days=days)).isoformat()
    body = http_get(API, params={
        "filter": f"issn:{issn},from-created-date:{d_from}",
        "rows": "0",
    }, mailto=mailto)
    return body.get("message", {}).get("total-results", 0)
