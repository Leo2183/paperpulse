import json
from datetime import date
from pathlib import Path

import pytest

from sm_scan import queries
from sm_scan.sources import biorxiv
from sm_scan.sources.base import SourceError

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
    assert set(p["matched_groups"]) == {"植物+空间成像", "MALDI/MSI方法学"}


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

    from sm_scan.sources import base

    with pytest.raises(SourceError):
        base._raise_for_status(FakeResp())
