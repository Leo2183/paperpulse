"""Europe PMC 适配器(spec §3.1):POST searchPOST + resultType=core + 游标翻页。

失败识别:HTTP 200 也可能带 errCode / 缺 resultList,显式检查。
"""

import logging
from collections.abc import Iterator
from datetime import date

from .. import engine, store
from .base import SourceError, http_post

log = logging.getLogger(__name__)

SEARCH_URL = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/searchPOST"
)


def _authors_display(author_string: str | None) -> str | None:
    if not author_string:
        return None
    first = author_string.split(",")[0].strip().rstrip(";")
    if "," in author_string:
        return f"{first} et al."
    return first


def _to_paper(r: dict) -> store.Paper:
    doi = engine.normalize_doi(r.get("doi"))
    title = (r.get("title") or "").strip()
    url = f"https://doi.org/{doi}" if doi else None
    # 字段名对齐真实 core 响应(第06轮 P0-1):
    # journalInfo.journal.title / firstIndexDate(顶层 journalTitle/firstIndexationDate 不存在)
    journal = (r.get("journalInfo") or {}).get("journal", {}).get("title")
    abstract = r.get("abstractText")
    if abstract and "<" in abstract:  # P2-13: 剥 <h4> 等 HTML 标签
        import html as _html
        import re as _re
        abstract = _html.unescape(_re.sub(r"<[^>]+>", "", abstract)).strip()
    return store.Paper(
        id=engine.paper_key(doi, title),
        doi=doi,
        pmcid=r.get("pmcid"),
        title=title,
        authors=_authors_display(r.get("authorString")),
        journal=journal,
        pub_date=r.get("firstPublicationDate"),
        indexed_date=r.get("firstIndexDate"),
        source="europepmc",
        sources=["europepmc"],
        subscription=None,
        url=url,
        abstract=abstract or None,
        tags=[],
        matched_groups=[],
        status="unread",
        starred=0,
        to_read=0,
        llm_digest=None,
        first_seen=None,
        updated_at=None,
    )


def search(query: str, d_from: date, d_to: date, page_size: int = 100) -> Iterator[store.Paper]:
    """按查询串翻页拉取;HTTP 200 + errCode/无 resultList 视为失败。"""
    cursor = "*"
    while True:
        data = http_post(SEARCH_URL, {
            "query": query,
            "format": "json",
            "resultType": "core",
            "pageSize": str(page_size),
            "cursorMark": cursor,
        })
        if "errCode" in data or "resultList" not in data:
            raise SourceError(
                f"Europe PMC error: {data.get('errCode')} {data.get('errMsg')}"
            )
        results = data["resultList"].get("result", [])
        for r in results:
            try:
                yield _to_paper(r)
            except Exception:  # 单条坏记录不中断整页
                log.exception("bad Europe PMC record: %r", r)
        nxt = data.get("nextCursorMark")
        if not results or not nxt or nxt == cursor:
            return
        cursor = nxt
