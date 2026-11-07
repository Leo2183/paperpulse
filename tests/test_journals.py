import json
from datetime import date

import pytest

from paperpulse import store
from paperpulse.sources import journals


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from paperpulse import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    c = store.connect()
    store.init_db(c)
    return c


def _paper(pid, title, abstract="Abs.", **kw):
    from paperpulse.store import Paper

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
        "paperpulse.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(
        "paperpulse.sources.journals.crossref.fetch_journal",
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
        "paperpulse.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(
        "paperpulse.sources.journals.crossref.fetch_journal",
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
        "paperpulse.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(
        "paperpulse.sources.journals.crossref.fetch_journal",
        lambda issn, a, b, mailto=None, today=None: iter([]))
    list(journals.scan_journals(conn, (date(2026, 11, 4), TODAY), TODAY))
    n = conn.execute("SELECT COUNT(*) FROM journal_subscriptions").fetchone()[0]
    assert n == 32
    fulls = conn.execute(
        "SELECT COUNT(*) FROM journal_subscriptions WHERE mode='full'").fetchone()[0]
    filters = conn.execute(
        "SELECT COUNT(*) FROM journal_subscriptions WHERE mode='filter'").fetchone()[0]
    assert fulls == 12 and filters == 20  # FC:X 迁 filter 后(P1-7)
    # IJMS 降 filter 保留(用户要求宽覆盖)
    ijms = conn.execute(
        "SELECT mode FROM journal_subscriptions WHERE name LIKE '%Molecular Sciences%'").fetchone()
    assert ijms["mode"] == "filter"


def test_full_journal_safety_valve_only_on_daily_window(conn, monkeypatch):
    # 窗口 5 天:JASMS(full)35 条 → 降级 filter(仅产出命中 BROAD 的)
    papers = [_paper(f"10.9/{i}", f"MALDI study number {i}") for i in range(30)]
    papers += [_paper(f"10.9/x{i}", f"Unrelated ion topic {i}") for i in range(5)]

    monkeypatch.setattr(
        "paperpulse.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter(papers))
    monkeypatch.setattr(
        "paperpulse.sources.journals.crossref.fetch_journal",
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
        "paperpulse.sources.journals.europepmc.search",
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

    monkeypatch.setattr("paperpulse.sources.journals.europepmc.search", fake_search)
    monkeypatch.setattr(
        "paperpulse.sources.journals.crossref.fetch_journal",
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
        "paperpulse.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr("paperpulse.sources.journals.crossref.fetch_journal", fake_fetch)
    store.seed_journals(conn, journals.DEFAULT_JOURNAL_ROWS)
    jid = conn.execute(
        "SELECT id FROM journal_subscriptions WHERE name LIKE 'International Journal of Mass%'").fetchone()[0]
    journals.route(conn, jid, (date(2026, 11, 1), TODAY), mailto=settings_mailto)
    assert seen["mailto"] == settings_mailto


def test_food_chem_x_is_filter():
    """P1-7:FC:X 90 天 ≈720 条,按自家 ≤300 规则应判 filter。"""
    fc = next(r for r in journals.DEFAULT_JOURNAL_ROWS
              if r["name"] == "Food Chemistry: X")
    assert fc["mode"] == "filter"


def test_audit_modes_reports_mismatch(monkeypatch, conn):
    monkeypatch.setattr(
        journals.crossref, "count_recent",
        lambda issn, days=90, mailto=None: 75 if issn == "1044-0305" else 400)
    monkeypatch.setattr(
        journals.europepmc, "search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(
        journals.crossref, "fetch_journal",
        lambda issn, a, b, mailto=None, today=None: iter([]))
    store.seed_journals(conn, journals.DEFAULT_JOURNAL_ROWS)
    rows = journals.audit_modes(conn)
    by_name = {r["name"]: r for r in rows}
    assert by_name["Journal of the American Society for Mass Spectrometry"]["mismatch"] is False
    # TrAC 当前 full,实测 400 条 → 建议 filter → mismatch
    assert by_name["TrAC Trends in Analytical Chemistry"]["mismatch"] is True
    # filter 刊 400 条与建议一致 → 不 mismatch
    assert by_name["Nature"]["mismatch"] is False


def test_preset_journals_loading(tmp_path, monkeypatch):
    import json

    (tmp_path / "t.json").write_text(json.dumps(
        {"journals": [{"name": "Neuron", "issn": "0896-6273", "mode": "filter"}]}),
        encoding="utf-8")
    monkeypatch.setattr(journals, "PRESETS_DIR", tmp_path)
    rows = journals.get_default_journals()
    assert rows == [dict(name="Neuron", issn="0896-6273", mode="filter")]

    monkeypatch.setattr(journals, "PRESETS_DIR", tmp_path / "none")
    assert len(journals.get_default_journals()) == 32  # 内置兜底


def test_subscription_export_import_roundtrip(conn, monkeypatch):
    from paperpulse.sources import journals as j

    monkeypatch.setattr(j.europepmc, "search",
                        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(j.crossref, "fetch_journal",
                        lambda issn, a, b, mailto=None, today=None: iter([]))
    store.seed_journals(conn, j.DEFAULT_JOURNAL_ROWS)
    data = j.export_subscriptions(conn)
    assert len(data["journals"]) == 32
    # 导入:同一批重复导入不产生重复;新刊合并
    added = j.import_subscriptions(conn, data)
    assert added == 0
    added2 = j.import_subscriptions(conn, {"journals": [
        dict(name="Neuron", issn="0896-6273", mode="filter")]})
    assert added2 == 1
    n = conn.execute("SELECT COUNT(*) FROM journal_subscriptions").fetchone()[0]
    assert n == 33


def test_safety_valve_daily_rate_not_window_sum(conn, monkeypatch):
    """P2-1:阈值按日均(>30/天),不是窗口合计。"""
    papers = [_paper(f"10.9/d{i}", f"MALDI study {i}") for i in range(35)]
    store.seed_journals(conn, journals.DEFAULT_JOURNAL_ROWS)
    jid = conn.execute(
        "SELECT id FROM journal_subscriptions WHERE name LIKE 'Journal of the American%'").fetchone()[0]

    def run(window):
        monkeypatch.setattr(
            "paperpulse.sources.journals.europepmc.search",
            lambda q, a, b, page_size=100: iter(papers))
        out = list(journals.scan_journals(conn, window, TODAY))
        return [j.get("_demoted") for _, j in out if j["id"] == jid]

    # 3 天窗口 35 条 ≈ 11.7/天 → 不降级
    assert not any(run((date(2026, 11, 4), TODAY)))
    # 1 天窗口 35 条 → 降级
    assert any(run((TODAY, TODAY)))
