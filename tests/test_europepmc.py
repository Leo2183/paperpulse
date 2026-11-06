import json
from datetime import date
from pathlib import Path

import pytest

from sm_scan.sources import europepmc
from sm_scan.sources.base import SourceError

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture
def pages(monkeypatch):
    responses = [_load("europepmc_core_p1.json"), _load("europepmc_core_p2.json")]
    calls = []

    def fake_post(url, data):
        calls.append((url, dict(data)))
        return responses.pop(0)

    monkeypatch.setattr(europepmc, "http_post", fake_post)
    return calls


def test_search_pagination_and_core_params(pages):
    out = list(europepmc.search("Q", date(2026, 11, 1), date(2026, 11, 6)))
    assert len(out) == 2
    assert [p["title"] for p in out] == [
        "Paper A about MALDI imaging", "Paper B about metabolite annotation",
    ]
    first = pages[0]
    url, data = first
    assert "searchPOST" in url
    assert data["resultType"] == "core"
    assert data["format"] == "json"
    assert data["cursorMark"] == "*"
    assert data["pageSize"] == "100"
    assert data["query"] == "Q"
    # 第二页从 nextCursorMark 继续
    assert pages[1][1]["cursorMark"] == "CUR2"


def test_search_field_mapping(pages):
    a = list(europepmc.search("Q", date(2026, 11, 1), date(2026, 11, 6)))[0]
    assert a["id"] == "10.1/a"
    assert a["pmcid"] == "PMC401"
    assert a["pub_date"] == "2026-11-01"
    assert a["indexed_date"] == "2026-11-02"
    assert a["journal"] == "Journal A"
    assert a["authors"] == "Alice A et al."
    assert a["source"] == "europepmc"
    assert a["abstract"] == "Abstract A."


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
