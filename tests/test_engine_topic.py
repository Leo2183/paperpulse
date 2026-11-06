import json
from datetime import date

import pytest

from sm_scan import engine, store


def _paper(pid, title, abstract="Abs.", **kw):
    base = dict(
        id=pid, doi=pid, pmcid=None, title=title, authors="A et al.",
        journal="J", pub_date="2026-11-01", indexed_date="2026-11-02",
        source="europepmc", sources=["europepmc"], subscription=None,
        url=f"https://doi.org/{pid}", abstract=abstract, tags=[],
        matched_groups=[], status="unread", starred=0, to_read=0,
        llm_digest=None, first_seen=None, updated_at=None,
    )
    base.update(kw)
    return store.Paper(base)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from sm_scan import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    c = store.connect()
    store.init_db(c)
    return c


def test_topic_scan_union_dedup_and_group_accumulation(conn, monkeypatch, capsys):
    queues = [
        [_paper("10.1/a", "MALDI imaging of plant")],
        [_paper("10.1/a", "MALDI imaging of plant"), _paper("10.1/b", "MALDI protein profile")],
        [],
    ]
    calls = []

    def fake_search(query, d_from, d_to, page_size=100):
        calls.append(query)
        return iter(queues.pop(0))

    monkeypatch.setattr("sm_scan.sources.europepmc.search", fake_search)
    rc = engine.run_scan(conn=conn)
    assert rc == 0
    assert len(calls) == 3  # 三组各一次
    n = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    assert n == 2  # 并集去重
    mg = json.loads(conn.execute(
        "SELECT matched_groups FROM papers WHERE id='10.1/a'").fetchone()[0])
    assert set(mg) == {"植物+空间成像", "MALDI/MSI方法学"}
    run = conn.execute("SELECT status, new_count FROM runs ORDER BY id DESC").fetchone()
    assert run["status"] == "ok" and run["new_count"] == 2
    # 组1 命中的论文带标签
    tags = json.loads(conn.execute(
        "SELECT tags FROM papers WHERE id='10.1/a'").fetchone()[0])
    assert "成像/空间" in tags and "植物" in tags


def test_dry_run_prints_no_write(conn, monkeypatch, capsys):
    def fake_search(query, d_from, d_to, page_size=100):
        return iter([_paper("10.1/x", "X")])

    monkeypatch.setattr("sm_scan.sources.europepmc.search", fake_search)
    rc = engine.run_scan(conn=conn, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "FIRST_IDATE" in out and "植物+空间成像" in out
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0


def test_all_sources_fail_exit_3(conn, monkeypatch):
    from sm_scan.sources.base import SourceError

    def fake_search(query, d_from, d_to, page_size=100):
        raise SourceError("down")

    monkeypatch.setattr("sm_scan.sources.europepmc.search", fake_search)
    rc = engine.run_scan(conn=conn)
    assert rc == 3
    run = conn.execute("SELECT status FROM runs ORDER BY id DESC").fetchone()
    assert run["status"] == "failed"


def test_seeds_default_groups_on_first_run(conn, monkeypatch):
    monkeypatch.setattr(
        "sm_scan.sources.europepmc.search",
        lambda q, a, b, page_size=100: iter([]),
    )
    engine.run_scan(conn=conn)
    n = conn.execute("SELECT COUNT(*) FROM term_groups").fetchone()[0]
    assert n == 3


def test_disabled_source_skipped(conn, monkeypatch):
    store.save_setting(conn, "src_europepmc_enabled", "0")
    called = []

    def fake_search(query, d_from, d_to, page_size=100):
        called.append(query)
        return iter([])

    monkeypatch.setattr("sm_scan.sources.europepmc.search", fake_search)
    rc = engine.run_scan(conn=conn)
    assert rc == 0
    assert called == []
