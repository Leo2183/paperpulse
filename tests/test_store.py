import sqlite3

import pytest

from sm_scan import store


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from sm_scan import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    c = store.connect()
    store.init_db(c)
    return c


def _paper(**kw):
    base = dict(
        id="10.1/x",
        doi="10.1/x",
        pmcid=None,
        title="A title",
        authors="Author A et al.",
        journal="Journal",
        pub_date="2026-11-01",
        indexed_date="2026-11-02",
        source="europepmc",
        sources=["europepmc"],
        subscription=None,
        url="https://doi.org/10.1/x",
        abstract=None,
        tags=[],
        matched_groups=[],
        status="unread",
        starred=0,
        to_read=0,
        llm_digest=None,
        first_seen=None,
        updated_at=None,
    )
    base.update(kw)
    return base


def test_init_db_idempotent(conn):
    store.init_db(conn)  # 二连不抛
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"papers", "runs", "term_groups", "journal_subscriptions", "settings"} <= names


def test_settings_defaults(conn):
    s = store.load_settings(conn)
    assert s["schema_version"] == "1"
    assert s["backfill_days"] == "30"
    assert s["src_europepmc_enabled"] == "1"
    assert s["src_journals_enabled"] == "1"
    assert s["src_biorxiv_enabled"] == "1"
    assert s["crossref_mailto"] == ""


def test_save_setting_roundtrip(conn):
    store.save_setting(conn, "llm_model", "glm-test")
    assert store.load_settings(conn)["llm_model"] == "glm-test"


def test_upsert_new_returns_true(conn):
    assert store.upsert_paper(conn, _paper()) is True
    row = conn.execute("SELECT title, first_seen, updated_at FROM papers").fetchone()
    assert row[0] == "A title"
    assert row[1] and row[2]


def test_upsert_seen_updates_abstract_returns_false(conn):
    store.upsert_paper(conn, _paper(abstract=None))
    assert store.upsert_paper(conn, _paper(abstract="Now we have one.")) is False
    n = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    abstract = conn.execute("SELECT abstract FROM papers").fetchone()[0]
    assert n == 1
    assert abstract == "Now we have one."


def test_matched_groups_accumulate_union(conn):
    store.upsert_paper(conn, _paper(matched_groups=["组3"]))
    store.upsert_paper(conn, _paper(matched_groups=["组1", "组3"]))
    store.upsert_paper(conn, _paper(matched_groups=["组2"]))
    mg = conn.execute("SELECT matched_groups FROM papers").fetchone()[0]
    import json

    assert set(json.loads(mg)) == {"组1", "组3", "组2"}


def test_subscription_first_nonnull_wins(conn):
    store.upsert_paper(conn, _paper(subscription=None))
    store.upsert_paper(conn, _paper(subscription="Nature"))
    store.upsert_paper(conn, _paper(subscription="Science"))
    sub = conn.execute("SELECT subscription FROM papers").fetchone()[0]
    assert sub == "Nature"


def test_tags_frozen_after_first_write(conn):
    store.upsert_paper(conn, _paper(tags=["植物"]))
    store.upsert_paper(conn, _paper(tags=[]))
    tags = conn.execute("SELECT tags FROM papers").fetchone()[0]
    import json

    assert json.loads(tags) == ["植物"]


def test_seed_groups_only_when_empty(conn):
    rows = [
        dict(name="组1", enabled=1, terms=[{"phrase": "MALDI"}], refine=[])
    ]
    store.seed_groups(conn, rows)
    store.seed_groups(conn, rows)
    n = conn.execute("SELECT COUNT(*) FROM term_groups").fetchone()[0]
    assert n == 1


def test_seed_journals_only_when_empty(conn):
    rows = [dict(name="Nature", issn="0028-0836", mode="filter", route="europepmc", enabled=1)]
    store.seed_journals(conn, rows)
    store.seed_journals(conn, rows)
    n = conn.execute("SELECT COUNT(*) FROM journal_subscriptions").fetchone()[0]
    assert n == 1


def test_lock_retry_logic(monkeypatch):
    calls = {"n": 0}
    sleeps = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return 42

    monkeypatch.setattr(store.time, "sleep", lambda s: sleeps.append(s))
    assert store._with_lock_retry(flaky) == 42
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_upsert_respects_provided_first_seen(conn):
    store.upsert_paper(conn, _paper(first_seen="2026-11-06T23:59:00"))
    fs = conn.execute("SELECT first_seen FROM papers").fetchone()[0]
    assert fs == "2026-11-06T23:59:00"
