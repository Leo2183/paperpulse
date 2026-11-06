import json
from datetime import date

import pytest

from sm_scan import store
from sm_scan.sources import journals


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from sm_scan import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    c = store.connect()
    store.init_db(c)
    return c


def _paper(pid, title, abstract="Abs.", **kw):
    from sm_scan.store import Paper

    base = dict(
        id=pid, doi=pid, pmcid=None, title=title, authors="A et al.",
        journal="J", pub_date="2026-11-01", indexed_date="2026-11-02",
        source="europepmc", sources=["europepmc"], subscription=None,
        url=f"https://doi.org/{pid}", abstract=abstract, tags=[],
        matched_groups=[], status="unread", starred=0, to_read=0,
        llm_digest=None, first_seen=None, updated_at=None,
    )
    base.update(kw)
    return Paper(base)


TODAY = date(2026, 11, 6)


def test_suggest_mode_threshold():
    assert journals.suggest_mode(300) == "full"
    assert journals.suggest_mode(301) == "filter"
    assert journals.suggest_mode(0) == "full"


def test_broad_terms_hits_and_misses():
    assert journals._broad_hit("Metabolomics profiling of plant root", "text")
    assert journals._broad_hit("Title only", "the metabolite was measured")
    assert not journals._broad_hit("Cardiac imaging outcomes", "MRI study")
    # DESI 是整词,不是词干:design/designer 不得误命中
    assert not journals._broad_hit("Designer nanoparticles", "smart design")
    assert journals._broad_hit("DESI-MS imaging", None)


def test_broad_filter_without_abstract_title_only():
    # 无摘要 → 仅标题匹配;标题不命中时正文词不存在,不救
    assert journals._broad_hit("MALDI imaging of tissue", None)
    assert not journals._broad_hit("Ion optics improvements", None)


def test_route_prefers_europepmc_falls_back(conn, monkeypatch):
    monkeypatch.setattr(
        "sm_scan.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(
        "sm_scan.sources.journals.crossref.fetch_journal",
        lambda issn, a, b, mailto=None, today=None: iter([_paper("10.9/x", "X")]))
    store.seed_journals(conn, journals.DEFAULT_JOURNAL_ROWS)
    jid = conn.execute(
        "SELECT id FROM journal_subscriptions WHERE name LIKE 'International Journal of Mass%'").fetchone()[0]
    win = (date(2026, 11, 1), TODAY)
    r = journals.route(conn, jid, win)
    assert r == "crossref"
    row = conn.execute(
        "SELECT route FROM journal_subscriptions WHERE id=?", (jid,)).fetchone()
    assert row["route"] == "crossref"


def test_route_double_zero_not_cached(conn, monkeypatch):
    monkeypatch.setattr(
        "sm_scan.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(
        "sm_scan.sources.journals.crossref.fetch_journal",
        lambda issn, a, b, mailto=None, today=None: iter([]))
    store.seed_journals(conn, journals.DEFAULT_JOURNAL_ROWS)
    jid = 1
    r = journals.route(conn, jid, (date(2026, 11, 1), TODAY))
    assert r == ""
    row = conn.execute(
        "SELECT route FROM journal_subscriptions WHERE id=?", (jid,)).fetchone()
    assert row["route"] == ""  # 不缓存,下轮重试


def test_seeds_thirty_two_journals_and_modes(conn, monkeypatch):
    monkeypatch.setattr(
        "sm_scan.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(
        "sm_scan.sources.journals.crossref.fetch_journal",
        lambda issn, a, b, mailto=None, today=None: iter([]))
    list(journals.scan_journals(conn, (date(2026, 11, 4), TODAY), TODAY))
    n = conn.execute("SELECT COUNT(*) FROM journal_subscriptions").fetchone()[0]
    assert n == 32
    fulls = conn.execute(
        "SELECT COUNT(*) FROM journal_subscriptions WHERE mode='full'").fetchone()[0]
    filters = conn.execute(
        "SELECT COUNT(*) FROM journal_subscriptions WHERE mode='filter'").fetchone()[0]
    assert fulls == 13 and filters == 19
    # IJMS 降 filter 保留(用户要求宽覆盖)
    ijms = conn.execute(
        "SELECT mode FROM journal_subscriptions WHERE name LIKE '%Molecular Sciences%'").fetchone()
    assert ijms["mode"] == "filter"


def test_full_journal_safety_valve_only_on_daily_window(conn, monkeypatch):
    # 窗口 5 天:JASMS(full)35 条 → 降级 filter(仅产出命中 BROAD 的)
    papers = [_paper(f"10.9/{i}", f"MALDI study number {i}") for i in range(30)]
    papers += [_paper(f"10.9/x{i}", f"Unrelated ion topic {i}") for i in range(5)]

    monkeypatch.setattr(
        "sm_scan.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter(papers))
    monkeypatch.setattr(
        "sm_scan.sources.journals.crossref.fetch_journal",
        lambda issn, a, b, mailto=None, today=None: iter([]))
    store.seed_journals(conn, journals.DEFAULT_JOURNAL_ROWS)
    jid = conn.execute(
        "SELECT id FROM journal_subscriptions WHERE name LIKE 'Journal of the American%'").fetchone()[0]
    out = list(journals.scan_journals(conn, (TODAY, TODAY), TODAY))
    out_j = [p for p, j in out if j["id"] == jid]
    assert len(out_j) < 35  # 部分被 BROAD 过滤
    assert any(j.get("_demoted") for _, j in out if j["id"] == jid)

    # 窗口 90 天(回填):同样 35 条不降级、全收
    monkeypatch.setattr(
        "sm_scan.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter(papers))
    out2 = list(journals.scan_journals(
        conn, (date(2026, 8, 8), TODAY), TODAY))
    out2_j = [p for p, j in out2 if j["id"] == jid]
    assert len(out2_j) == 35
    assert not any(j.get("_demoted") for _, j in out2 if j["id"] == jid)


def test_backfill_journal_uses_90_days(conn, monkeypatch):
    windows = []

    def fake_search(q, a, b, page_size=100):
        windows.append((a, b))
        return iter([])

    monkeypatch.setattr("sm_scan.sources.journals.europepmc.search", fake_search)
    monkeypatch.setattr(
        "sm_scan.sources.journals.crossref.fetch_journal",
        lambda issn, a, b, mailto=None, today=None: iter([]))
    store.seed_journals(conn, journals.DEFAULT_JOURNAL_ROWS)
    journals.backfill_journal(conn, 1, today=TODAY)
    assert windows and (TODAY - windows[0][0]).days == 90


def test_probe_passes_mailto_to_crossref(conn, monkeypatch):
    settings_mailto = "me@example.org"
    seen = {}

    def fake_fetch(issn, a, b, mailto=None, today=None):
        seen["mailto"] = mailto
        return iter([_paper("10.9/m", "M")])

    monkeypatch.setattr(
        "sm_scan.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr("sm_scan.sources.journals.crossref.fetch_journal", fake_fetch)
    store.seed_journals(conn, journals.DEFAULT_JOURNAL_ROWS)
    jid = conn.execute(
        "SELECT id FROM journal_subscriptions WHERE name LIKE 'International Journal of Mass%'").fetchone()[0]
    journals.route(conn, jid, (date(2026, 11, 1), TODAY), mailto=settings_mailto)
    assert seen["mailto"] == settings_mailto
