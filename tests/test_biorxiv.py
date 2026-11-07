import json
from datetime import date
from pathlib import Path

import pytest

from paperpulse import queries
from paperpulse.sources import biorxiv
from paperpulse.sources.base import SourceError

FIX = Path(__file__).parent / "fixtures"
GROUPS = queries.DEFAULT_GROUPS


@pytest.fixture
def page(monkeypatch):
    body = json.loads((FIX / "biorxiv_p1.json").read_text(encoding="utf-8"))
    calls = []

    def fake_get(url, params=None, mailto=None):
        calls.append(url)
        return body

    monkeypatch.setattr(biorxiv, "http_get", fake_get)
    return calls


def test_biorxiv_strips_jats_and_filters_by_groups(page):
    out = list(biorxiv.scan(date(2026, 11, 1), date(2026, 11, 6), GROUPS))
    # 只有第一条命中组1(空间+植物),CRISPR 被本地过滤
    assert len(out) == 1
    p = out[0]
    assert p["title"] == "Spatial metabolomics of plant leaves"
    assert "<" not in p["abstract"] and "MALDI imaging" in p["abstract"]
    assert p["source"] == "biorxiv"
    # 组2 ⊃ 组1(spec §4.2):双命中正确,报告层负责归一档
    assert set(p["matched_groups"]) == {"1", "2"}  # 组 id(P1-3)


def test_biorxiv_refine_and_semantics(page):
    # 组3 需要 terms 命中 AND refine 命中:CRISPR 条目连 terms 都不命中
    out = list(biorxiv.scan(date(2026, 11, 1), date(2026, 11, 6), GROUPS))
    assert all("产物注释" not in p["matched_groups"] for p in out)


def test_biorxiv_source_error_propagates(monkeypatch):
    def boom(url, params=None, mailto=None):
        raise SourceError("maintenance page")

    monkeypatch.setattr(biorxiv, "http_get", boom)
    with pytest.raises(SourceError):
        list(biorxiv.scan(date(2026, 11, 1), date(2026, 11, 6), GROUPS))


def test_base_non_json_response_raises_source_error():
    class FakeResp:
        status_code = 200
        text = "<html>maintenance</html>"

        def json(self):
            raise ValueError("not json")

    from paperpulse.sources import base

    with pytest.raises(SourceError):
        base._raise_for_status(FakeResp())


def test_pagination_follows_api_page_size_30(monkeypatch):
    """bioRxiv 每页 30 条(非 100):首页满 30 条必须继续翻页,不得提前停。"""
    import json as _json

    p1 = _json.loads((FIX / "biorxiv_p1_full.json").read_text(encoding="utf-8"))
    # 满页 30 条
    p1["collection"] = [dict(p1["collection"][i % 2], doi=f"10.1101/p1.{i}")
                        for i in range(30)]
    p1["messages"][0]["count"] = 30
    p2 = {"messages": [{"status": "ok", "count": 2, "total": 32}], "collection": [
        {"doi": "10.1101/p2.hit", "title": "Spatial metabolomics of root",
         "abstract": "imaging", "authors": "C C", "date": "2026-11-03"}
    ]}
    pages_seen = []

    def fake_get(url, params=None, mailto=None):
        pages_seen.append(url)
        return p1 if len(pages_seen) == 1 else p2

    monkeypatch.setattr(biorxiv, "http_get", fake_get)
    out = list(biorxiv.scan(date(2026, 11, 1), date(2026, 11, 6), GROUPS))
    assert len(pages_seen) >= 2, "首页满 30 条必须请求第二页"
    dois = {p["id"] for p in out}
    assert "10.1101/p2.hit" in dois
