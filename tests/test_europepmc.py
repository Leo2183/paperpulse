import json
from datetime import date
from pathlib import Path

import pytest

from paperpulse.sources import europepmc
from paperpulse.sources.base import SourceError

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture
def pages(monkeypatch):
    body = _load("europepmc_core_p1.json")  # 真实录制(评审第06轮 §六:fixture 必须是录的)
    calls = []

    def fake_post(url, data):
        calls.append((url, dict(data)))
        if len(calls) == 1:
            return body
        return {"resultList": {"result": []},
                "nextCursorMark": body.get("nextCursorMark", "*")}

    monkeypatch.setattr(europepmc, "http_post", fake_post)
    return calls, body


def test_search_pagination_and_core_params(pages):
    calls, body = pages
    out = list(europepmc.search("Q", date(2026, 11, 1), date(2026, 11, 6)))
    assert len(out) == len(body["resultList"]["result"])
    url, data = calls[0]
    assert "searchPOST" in url
    assert data["resultType"] == "core"
    assert data["format"] == "json"
    assert data["cursorMark"] == "*"
    assert data["pageSize"] == "100"


def test_search_field_mapping_uses_real_response_fields(pages):
    """P0-1:字段名对齐真实 core 响应(journalInfo.journal.title / firstIndexDate)。"""
    _, body = pages
    src = body["resultList"]["result"]
    out = list(europepmc.search("Q", date(2026, 11, 1), date(2026, 11, 6)))
    for p, r in zip(out, src):
        assert p["title"] == (r.get("title") or "").strip()
        assert p["journal"] == r["journalInfo"]["journal"]["title"]
        assert p["indexed_date"] == r["firstIndexDate"]
        assert p["pub_date"] == r["firstPublicationDate"]
        assert p["id"] == p["doi"].lower()


def test_fixture_contract_journal_info_present(pages):
    """字段存在性契约:录制的 fixture 每条必须有 journalInfo.journal.title。"""
    _, body = pages
    for r in body["resultList"]["result"]:
        assert r["journalInfo"]["journal"]["title"]
        assert r["firstIndexDate"]


def test_search_strips_html_from_abstract(pages):
    """P2-13:Europe PMC 摘要常含 <h4> 等标签,入库前剥除。"""
    _, body = pages
    src_with = next(r for r in body["resultList"]["result"]
                    if r.get("abstractText") and "<" in r["abstractText"])
    out = list(europepmc.search("Q", date(2026, 11, 1), date(2026, 11, 6)))
    p = next(x for x in out if x["doi"] == src_with["doi"].lower())
    assert "<" not in (p["abstract"] or "")
    assert len(p["abstract"]) > 100


def test_search_errcode_raises_source_error(monkeypatch):
    monkeypatch.setattr(
        europepmc, "http_post", lambda url, data: _load("europepmc_errcode.json")
    )
    with pytest.raises(SourceError):
        list(europepmc.search("BAD", date(2026, 11, 1), date(2026, 11, 6)))


def test_search_missing_result_list_raises(monkeypatch):
    monkeypatch.setattr(
        europepmc, "http_post", lambda url, data: {"version": "6.9", "hitCount": 0}
    )
    with pytest.raises(SourceError):
        list(europepmc.search("Q", date(2026, 11, 1), date(2026, 11, 6)))
