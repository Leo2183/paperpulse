import json
from datetime import date

import pytest

from paperpulse import report, store


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
        _paper("10.1/a", "Plant MSI core", matched_groups=["1"],
               tags=["植物", "成像/空间"]),
        # 二档:仅组2
        _paper("10.1/b", "MALDI methods", matched_groups=["2"],
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
    from paperpulse import config

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
    assert "二、MALDI/MSI方法学(1)" in text
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
    assert "摘要未提供" in text  # P3-5:措辞统一,不绑定来源
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


def test_export_todo_snapshot(conn):
    store.upsert_paper(conn, _paper("10.2/a", "Todo one", to_read=1,
                                    llm_digest="导读一。"))
    store.upsert_paper(conn, _paper("10.2/b", "Todo two", to_read=1))
    store.upsert_paper(conn, _paper("10.2/c", "Not todo"))
    path = report.export_todo(conn)
    text = path.read_text(encoding="utf-8")
    assert "Todo one" in text and "Todo two" in text and "Not todo" not in text
    assert "https://doi.org/10.2/a" in text
    assert "导读一。" in text
    assert path.name.startswith("todo-reading-")


def test_subscription_paper_with_group_hit_tiered_by_group(conn):
    """spec §12:订阅刊论文同时命中检索组 → 按组分档,不进补漏节。"""
    store.upsert_paper(conn, _paper(
        "10.3/a", "Plant MSI in Nature journal",
        matched_groups=["1"], subscription="Nature"))
    text = report.generate(conn, date(2026, 11, 6)).read_text(encoding="utf-8")
    assert "一、核心:植物+空间成像(1)" in text
    assert "订阅补漏 0" in text  # 头部分档行;空补漏不输出节


def test_tier_by_group_id_and_stale_excluded(conn):
    """P1-3 分档按组 id;P1-2 stale 论文不进日报。"""
    store.upsert_paper(conn, _paper("10.8/a", "Core by id",
                                    matched_groups=["1"]))
    stale = _paper("10.8/old", "Stale one")
    stale["_stale"] = True
    store.upsert_paper(conn, stale)
    text = report.generate(conn, date(2026, 11, 6)).read_text(encoding="utf-8")
    assert "一、核心:植物+空间成像(1)" in text
    assert "Stale one" not in text


def test_report_journal_counts_and_backfill_note(conn):
    """P1-7:补漏节前给每刊计数;首跑(库内 run 少)标注回填预期。"""
    for i in range(3):
        store.upsert_paper(conn, _paper(
            f"10.9/{i}", f"Nature paper {i}", subscription="Nature"))
    conn.execute("INSERT INTO runs (started_at, finished_at, status)"
                 " VALUES ('2026-11-06T09:00:00','2026-11-06T09:05:00','ok')")
    conn.commit()
    text = report.generate(conn, date(2026, 11, 6)).read_text(encoding="utf-8")
    assert "Nature × 3" in text
    assert "首跑回填" in text


def test_report_notes_demoted_journals(conn):
    """P2-2:安全阀降级的刊在日报可见。"""
    store.upsert_paper(conn, _paper("10.10/a", "Flood paper"))
    import json as _json

    conn.execute(
        "INSERT INTO runs (started_at, finished_at, status, new_count, per_source)"
        " VALUES ('2026-11-06T09:00:00','2026-11-06T09:05:00','ok',1,?)",
        (_json.dumps({"journals": {"demoted": ["Some Journal"]}}),))
    conn.commit()
    text = report.generate(conn, date(2026, 11, 6)).read_text(encoding="utf-8")
    assert "Some Journal" in text and "降级" in text


def test_entry_polish_no_mode_fallback_no_dup_marks(conn):
    """P3-5:刊名回退用订阅名而非模式;[订阅] 不印两遍;缺摘要措辞统一。"""
    store.upsert_paper(conn, _paper(
        "10.11/a", "Sub no journal", journal=None, subscription="Nature"))
    store.upsert_paper(conn, _paper("10.11/b", "No abs", abstract=None))
    text = report.generate(conn, date(2026, 11, 6)).read_text(encoding="utf-8")
    assert "*Nature*" in text and "*filter*" not in text and "*full*" not in text
    line = next(l for l in text.splitlines() if "10.11/a" in l or "Sub no journal" in l)
    src_line = next(l for l in text.splitlines()
                    if l.startswith(("Alice A", "A et al")) and "来源" in l)
    assert src_line.count("[订阅]") == 0  # 标记只在标题行,来源行不重复
    assert "摘要未提供" in text and "(Crossref)" not in text


def test_tiering_follows_active_preset_groups(conn):
    """P1-3:分档按当前 term_groups 位置,不认死内置 id/标题(换主题生效)。"""
    conn.execute("DELETE FROM term_groups")
    conn.executemany(
        "INSERT INTO term_groups (id, name, enabled, terms, refine) VALUES (?,?,?,?,?)",
        [(11, "microbiome core", 1, "[]", "[]"),
         (12, "omics methods", 1, "[]", "[]")])
    conn.commit()
    store.upsert_paper(conn, _paper("10.14/a", "Gut metagenome paper",
                                    matched_groups=["11"]))
    store.upsert_paper(conn, _paper("10.14/b", "Methods paper",
                                    matched_groups=["12"]))
    text = report.generate(conn, date(2026, 11, 6)).read_text(encoding="utf-8")
    assert "microbiome core" in text and "Gut metagenome paper" in text
    assert "omics methods" in text and "Methods paper" in text
    assert "植物+空间成像" not in text and "MALDI/MSI" not in text
