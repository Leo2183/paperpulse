import json
from datetime import date
from pathlib import Path

import pytest

from sm_scan.sources import crossref

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def ijms(monkeypatch):
    body = json.loads((FIX / "crossref_ijms.json").read_text(encoding="utf-8"))
    calls = []

    def fake_get(url, params=None, mailto=None):
        calls.append((url, params))
        return body

    monkeypatch.setattr(crossref, "http_get", fake_get)
    return calls


def test_fetch_filters_blacklist_and_parses(ijms):
    out = list(crossref.fetch_journal("1387-3806", date(2026, 11, 1), date(2026, 11, 6)))
    titles = [p["title"] for p in out]
    # 目录页与 Publisher's Note 被黑名单过滤
    assert "Graphical abstract Contents continued" not in titles
    assert "Publisher's Note" not in titles
    assert "Tandem MS of plant alkaloids" in titles
    by_id = {p["id"]: p for p in out}
    ok = by_id["10.1016/j.ijms.2026.11.001"]
    # jats 剥净 + HTML 实体解码
    assert "<" not in ok["abstract"] and "jats" not in ok["abstract"]
    assert "MS/MS" in ok["abstract"] and "&" in ok["abstract"]
    assert ok["authors"] == "Alice A et al."
    assert ok["pub_date"] == "2026-11-02"
    assert ok["source"] == "crossref"
    assert "_stale" not in ok
    # 无 abstract 条目 → None
    assert by_id["10.1016/j.ijms.2026.11.002"]["abstract"] is None


def test_fetch_marks_stale_old_registration(ijms):
    out = list(crossref.fetch_journal("1387-3806", date(2026, 11, 1), date(2026, 11, 6)))
    by_id = {p["id"]: p for p in out}
    # 2014 年论文:注册日保护 → _stale 标记
    assert by_id["10.1016/j.ijms.2014.02.011"]["_stale"] is True


def test_fetch_passes_mailto_and_filters(ijms):
    list(crossref.fetch_journal("1387-3806", date(2026, 11, 1), date(2026, 11, 6)))
    url, params = ijms[0]
    assert "api.crossref.org/works" in url
    assert "issn:1387-3806" in params["filter"]
    assert "from-created-date:2026-11-01" in params["filter"]


def test_resolve_journal_exact_title_first(monkeypatch):
    body = {"message": {"items": [
        {"title": "Nature Communications", "ISSN": ["2041-1723"]},
        {"title": "Nature", "ISSN": ["0028-0836"]},
        {"title": "Nature Methods", "ISSN": ["1548-7091"]},
    ]}}
    monkeypatch.setattr(crossref, "http_get", lambda u, params=None, mailto=None: body)
    out = crossref.resolve_journal("Nature")
    assert out[0] == ("Nature", "0028-0836")
    assert len(out) == 3


def test_count_recent(monkeypatch):
    monkeypatch.setattr(
        crossref, "http_get",
        lambda u, params=None, mailto=None: {"message": {"total-results": 84}})
    assert crossref.count_recent("1387-3806") == 84
