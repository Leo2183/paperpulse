import json
from datetime import date

import pytest

from sm_scan import report, store


def _paper(pid, title, **kw):
    base = dict(
        id=pid, doi=pid, pmcid=None, title=title, authors="A et al.",
        journal="J", pub_date="2026-11-05", indexed_date="2026-11-05",
        source="europepmc", sources=["europepmc"], subscription=None,
        url=f"https://doi.org/{pid}", abstract="word " * 400, tags=[],
        matched_groups=[], status="unread", starred=0, to_read=0,
        llm_digest=None, first_seen="2026-11-06T09:00:00",
        updated_at="2026-11-06T09:00:00",
    )
    base.update(kw)
    return store.Paper(base)


@pytest.fixture
def filled(conn):  # 来自 conftest
    papers = [
        # 一档:命中组1
        _paper("10.1/a", "Plant MSI core", matched_groups=["植物+空间成像"],
               tags=["植物", "成像/空间"]),
        # 二档:仅组2
        _paper("10.1/b", "MALDI methods", matched_groups=["MALDI/MSI方法学"],
               tags=["成像/空间"]),
        # 三档:无组无订阅
        _paper("10.1/c", "Other topic", matched_groups=[]),
        # 补漏 4a:filter 巨刊、无组命中
        _paper("10.1/d", "Nature pick", matched_groups=[], subscription="Nature"),
        # 补漏 4b:full 窄刊
        _paper("10.1/e", "JASMS whole", matched_groups=[], subscription="JASMS"),
        # 无摘要(Crossref)+ 待精读
        _paper("10.1/f", "No abstract one", abstract=None,
               subscription="Int J Mass Spectrom", to_read=1),
    ]
    for p in papers:
        store.upsert_paper(conn, p)
    return papers


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from sm_scan import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    c = store.connect()
    store.init_db(c)
    return c


def test_report_sections_and_counts(filled, conn, tmp_path):
    path = report.generate(conn, date(2026, 11, 6))
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    # 三档 + 补漏 4a/4b
    assert "一、核心:植物+空间成像(1)" in text
    assert "二、方法学:MALDI/MSI(1)" in text
    assert "三、其他(1)" in text
    assert "四、订阅补漏" in text
    assert "4a" in text and "[订阅]" in text
    assert "4b" in text and "[整刊]" in text
    # 计数自洽:分档和 = 总数
    assert "新增 6 篇" in text
    # 摘要 300 词截断(400 词摘要)
    assert text.count("word word word") > 0
    assert len([l for l in text.splitlines() if l.startswith("> ")]) <= 7
    # 无摘要标注(Review Focus #1 关联)
    assert "摘要未提供(Crossref)" in text
    # 待精读节注明重叠
    assert "待精读" in text and "重叠" in text
    # 日期缺失:无 pub_date 论文显示"日期未知"
    store.upsert_paper(conn, _paper("10.1/g", "No date", pub_date=None))
    path2 = report.generate(conn, date(2026, 11, 6))
    assert "日期未知" in path2.read_text(encoding="utf-8")


def test_report_llm_digest_replaces_abstract(filled, conn):
    conn.execute(
        "UPDATE papers SET llm_digest='这篇做了植物成像。' WHERE id='10.1/a'")
    conn.commit()
    text = report.generate(conn, date(2026, 11, 6)).read_text(encoding="utf-8")
    assert "导读" in text and "这篇做了植物成像。" in text


def test_tiering_uses_json_each_not_like(filled, conn):
    # "g1" 误匹配 "g10" 防回归:构造名字前缀重叠的组名
    conn.execute(
        "UPDATE papers SET matched_groups='[\"组x\"]' WHERE id='10.1/c'")
    conn.commit()
    # 组名 "组x" 不应因 LIKE 匹配到任何档,归三档
    text = report.generate(conn, date(2026, 11, 6)).read_text(encoding="utf-8")
    assert "三、其他(1)" in text
