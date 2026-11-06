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


@pytest.fixture(autouse=True)
def _no_other_tracks(monkeypatch):
    """默认把订阅与 biorxiv 轨道 mock 为空,防测试触真实网络;个别测试再覆盖。"""
    monkeypatch.setattr("sm_scan.sources.journals.scan_journals",
                        lambda c, w, t, mailto=None: iter([]))
    monkeypatch.setattr("sm_scan.sources.biorxiv.scan",
                        lambda a, b, g: iter([]))


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


def test_topic_track_only_fail_is_partial(conn, monkeypatch):
    from sm_scan.sources.base import SourceError

    def fake_search(query, d_from, d_to, page_size=100):
        raise SourceError("down")

    monkeypatch.setattr("sm_scan.sources.europepmc.search", fake_search)
    rc = engine.run_scan(conn=conn)
    assert rc == 2  # 仅主题轨失败,其余轨道空跑成功 → partial
    run = conn.execute("SELECT status FROM runs ORDER BY id DESC").fetchone()
    assert run["status"] == "partial"


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


def test_subscription_track_integration(conn, monkeypatch):
    from sm_scan.sources import journals as journals_mod

    p1 = _paper("10.5/a", "Nature paper on metabolism", source="europepmc")
    p2 = _paper("10.5/b", "Old registered paper", source="crossref")
    p2["_stale"] = True
    jrow = {"id": 1, "name": "Nature", "mode": "filter", "route": "europepmc", "enabled": 1}

    def fake_scan_journals(c, window, today, mailto=None):
        yield (p1, jrow)
        yield (p2, jrow)

    monkeypatch.setattr("sm_scan.sources.europepmc.search",
                        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(journals_mod, "scan_journals", fake_scan_journals)
    monkeypatch.setattr("sm_scan.sources.biorxiv.scan",
                        lambda a, b, g: iter([]))
    rc = engine.run_scan(conn=conn)
    assert rc == 0
    subs = conn.execute("SELECT id, subscription FROM papers").fetchall()
    assert all(r["subscription"] == "Nature" for r in subs)
    run = conn.execute("SELECT new_count, per_source FROM runs ORDER BY id DESC").fetchone()
    # stale 只入库不计新增
    assert run["new_count"] == 1
    assert "_stale" not in dict(conn.execute(
        "SELECT * FROM papers WHERE id='10.5/b'").fetchone())


def test_biorxiv_track_integration(conn, monkeypatch):
    from sm_scan.sources import biorxiv as brx

    preprint = _paper("10.1101/x", "Spatial metabolomics preprint",
                      source="biorxiv", matched_groups=["植物+空间成像"])
    monkeypatch.setattr("sm_scan.sources.europepmc.search",
                        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr("sm_scan.sources.journals.scan_journals",
                        lambda c, w, t, mailto=None: iter([]))
    monkeypatch.setattr(brx, "scan", lambda a, b, g: iter([preprint]))
    rc = engine.run_scan(conn=conn)
    assert rc == 0
    row = conn.execute("SELECT source FROM papers WHERE id='10.1101/x'").fetchone()
    assert row["source"] == "biorxiv"


def test_all_tracks_fail_exit_3(conn, monkeypatch):
    from sm_scan.sources.base import SourceError

    monkeypatch.setattr("sm_scan.sources.europepmc.search",
                        lambda q, a, b, page_size=100: (_ for _ in ()).throw(SourceError("down")))
    monkeypatch.setattr("sm_scan.sources.journals.scan_journals",
                        lambda c, w, t, mailto=None: (_ for _ in ()).throw(SourceError("down")))
    monkeypatch.setattr("sm_scan.sources.biorxiv.scan",
                        lambda a, b, g: (_ for _ in ()).throw(SourceError("down")))
    rc = engine.run_scan(conn=conn)
    assert rc == 3


def test_run_ts_assigns_first_seen_for_midnight_spanning_run(conn, monkeypatch):
    """跨零点 run 的所有论文 first_seen 归入起始时刻(spec §5)。"""
    monkeypatch.setattr(
        "sm_scan.sources.europepmc.search",
        lambda q, a, b, page_size=100: iter([_paper("10.7/a", "Midnight paper")]))
    engine.run_scan(conn=conn, run_ts="2026-11-05T23:58:00")
    fs = conn.execute("SELECT first_seen FROM papers WHERE id='10.7/a'").fetchone()[0]
    assert fs.startswith("2026-11-05T23:58")
