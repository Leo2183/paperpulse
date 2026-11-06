"""bioRxiv/medRxiv 适配器(spec §3.3):日期窗拉取 + 本地短语组过滤。

API 不支持检索式:整页拉取后按组本地匹配(terms OR + refine AND)。
标题/摘要含 JATS 标签,入库前剥除 + 解 HTML 实体。
"""

import html
import logging
import re
from collections.abc import Iterator
from datetime import date

from .. import engine, store
from .base import SourceError, http_get

log = logging.getLogger(__name__)

API = "https://api.biorxiv.org/details/biorxiv/{from_}/{to}/{page}"


def _clean(s: str | None) -> str | None:
    if not s:
        return None
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _group_hits(title: str, abstract: str | None, groups) -> list[str]:
    text = (title + "\n" + (abstract or "")).lower()
    hits = []
    for g in groups:
        if not any(t.phrase.lower() in text for t in g.terms):
            continue
        if g.refine and not any(t.phrase.lower() in text for t in g.refine):
            continue
        hits.append(g.name)
    return hits


def _to_paper(item: dict, groups) -> store.Paper | None:
    title = (_clean(item.get("title")) or "").strip()
    abstract = _clean(item.get("abstract"))
    matched = _group_hits(title, abstract, groups)
    if not matched:
        return None
    doi = engine.normalize_doi(item.get("doi"))
    return store.Paper(
        id=engine.paper_key(doi, title),
        doi=doi,
        pmcid=None,
        title=title,
        authors=(item.get("authors") or "").split(";")[0].strip() or None,
        journal="bioRxiv",
        pub_date=item.get("date"),
        indexed_date=None,
        source="biorxiv",
        sources=["biorxiv"],
        subscription=None,
        url=f"https://doi.org/{doi}" if doi else None,
        abstract=abstract,
        tags=[],
        matched_groups=matched,
        status="unread",
        starred=0,
        to_read=0,
        llm_digest=None,
        first_seen=None,
        updated_at=None,
    )


def scan(d_from: date, d_to: date, groups) -> Iterator[store.Paper]:
    """按日期窗逐页(整数页码)拉取,本地组过滤后产出。"""
    page = 0
    while True:
        url = API.format(from_=d_from.isoformat(), to=d_to.isoformat(), page=page)
        body = http_get(url)
        msg = (body.get("messages") or [{}])[0]
        if msg.get("status") != "ok":
            raise SourceError(f"bioRxiv status: {msg.get('status')}")
        items = body.get("collection", [])
        for it in items:
            try:
                p = _to_paper(it, groups)
            except Exception:
                log.exception("bad bioRxiv record: %r", it.get("doi"))
                continue
            if p is not None:
                yield p
        if len(items) < 100:
            return
        page += 1
