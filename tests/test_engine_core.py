from datetime import date

from sm_scan import engine


def test_normalize_doi_variants():
    assert engine.normalize_doi("https://doi.org/10.1016/J.TALANTA.2026.01.001") == "10.1016/j.talanta.2026.01.001"
    assert engine.normalize_doi("doi:10.1016/j.talanta.2026.01.001") == "10.1016/j.talanta.2026.01.001"
    assert engine.normalize_doi("info:doi/10.1016/j.talanta.2026.01.001") == "10.1016/j.talanta.2026.01.001"
    assert engine.normalize_doi("10.1016/J.TALANTA.2026.01.001") == "10.1016/j.talanta.2026.01.001"
    # 分号拼接取首段(Review Focus #4)
    assert engine.normalize_doi("10.1016/j.x.2024.01.001; 10.1016/j.x.2024.01.001b") == "10.1016/j.x.2024.01.001"
    assert engine.normalize_doi(None) is None
    assert engine.normalize_doi("  ") is None


def test_paper_key_title_hash_stable():
    a = engine.paper_key(None, "MALDI imaging  of plant roots!")
    b = engine.paper_key(None, "maldi imaging OF plant roots")
    assert a == b
    assert a.startswith("titlehash:")
    # DOI 优先
    assert engine.paper_key("10.1/x", "whatever") == "10.1/x"


def test_scan_window_first_run_backfill():
    w = engine.scan_window(None, date(2026, 11, 6))
    assert w == (date(2026, 10, 7), date(2026, 11, 6))


def test_scan_window_anchors_finished_minus_3d():
    w = engine.scan_window(date(2026, 11, 5), date(2026, 11, 6))
    assert w == (date(2026, 11, 2), date(2026, 11, 6))


def test_scan_window_does_not_expand_across_runs():
    # 第2轮起点 11-02;第3轮 finished=11-06 → 起点 11-03,不早于上轮(不扩张)
    w2 = engine.scan_window(date(2026, 11, 5), date(2026, 11, 6))
    w3 = engine.scan_window(date(2026, 11, 6), date(2026, 11, 7))
    assert w3[0] >= w2[0]


def test_scan_window_biorxiv_backfill_seven_days():
    w = engine.scan_window(None, date(2026, 11, 6), biorxiv=True)
    assert w == (date(2026, 10, 30), date(2026, 11, 6))


def test_dates_ok_tolerates_missing_and_partial_dates():
    # Review Focus #1:pub_date 缺失/只有年月不抛
    assert engine.dates_ok(None, (date(2026, 11, 1), date(2026, 11, 6))) is True
    assert engine.dates_ok("2026-11", (date(2026, 11, 1), date(2026, 11, 6))) is True
    assert engine.dates_ok("2026-11-02", (date(2026, 11, 1), date(2026, 11, 6))) is True
    assert engine.dates_ok("2026-10-01", (date(2026, 11, 1), date(2026, 11, 6))) is False
