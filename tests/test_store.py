import sqlite3

import pytest

from paperpulse import store


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from paperpulse import config

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
    assert s["schema_version"] == "2"  # v2:stale 列 + 组 id 迁移
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


def test_abstract_monotonic_fill(conn):
    """spec §5 摘要从无到有:后见无摘要(Crossref)不得抹掉先存摘要。"""
    store.upsert_paper(conn, _paper(abstract="Europe PMC abstract."))
    store.upsert_paper(conn, _paper(abstract=None))
    abstract = conn.execute("SELECT abstract FROM papers").fetchone()[0]
    assert abstract == "Europe PMC abstract."


def test_upsert_stale_column(conn):
    p1 = _paper(id="10.6/a")
    p1["_stale"] = True
    store.upsert_paper(conn, p1)
    store.upsert_paper(conn, _paper(id="10.6/b"))
    vals = dict(conn.execute(
        "SELECT id, COALESCE(stale,0) FROM papers").fetchall())
    assert vals["10.6/a"] == 1 and vals["10.6/b"] == 0


def test_migration_v1_to_v2_group_ids_and_stale(tmp_path, monkeypatch):
    """P1-2/P1-3 存量迁移:加 stale 列;matched_groups 组名 → 组 id。"""
    from paperpulse import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    import sqlite3

    db = tmp_path / "data" / "scan.db"
    db.parent.mkdir()
    raw = sqlite3.connect(db)
    raw.executescript("""
    CREATE TABLE papers (
      id TEXT PRIMARY KEY, doi TEXT, pmcid TEXT, title TEXT NOT NULL,
      authors TEXT, journal TEXT, pub_date TEXT, indexed_date TEXT,
      source TEXT NOT NULL, sources TEXT DEFAULT '[]', subscription TEXT,
      url TEXT, abstract TEXT, tags TEXT DEFAULT '[]',
      matched_groups TEXT DEFAULT '[]', status TEXT DEFAULT 'unread',
      starred INTEGER DEFAULT 0, to_read INTEGER DEFAULT 0,
      llm_digest TEXT, first_seen TEXT NOT NULL, updated_at TEXT);
    CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT,
      finished_at TEXT, status TEXT, new_count INTEGER, per_source TEXT, error TEXT);
    CREATE TABLE term_groups (id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, enabled INTEGER DEFAULT 1,
      terms TEXT NOT NULL, refine TEXT NOT NULL);
    CREATE TABLE journal_subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, issn TEXT NOT NULL, mode TEXT NOT NULL,
      route TEXT NOT NULL, enabled INTEGER DEFAULT 1, added_at TEXT);
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
    INSERT INTO settings VALUES ('schema_version','1');
    INSERT INTO term_groups (id, name, enabled, terms, refine) VALUES
      (1,'植物+空间成像',1,'[]','[]'),
      (2,'MALDI/MSI方法学',1,'[]','[]'),
      (3,'产物注释',1,'[]','[]');
    INSERT INTO papers (id, title, source, first_seen, matched_groups)
      VALUES ('10.7/a','T','europepmc','2026-11-06T09:00:00',
              '["植物+空间成像","产物注释"]');
    """)
    raw.commit()
    raw.close()
    c = store.connect()
    store.init_db(c)  # 触发 v1→v2 迁移
    mg = c.execute("SELECT matched_groups FROM papers WHERE id='10.7/a'").fetchone()[0]
    import json
    assert sorted(json.loads(mg)) == ["1", "3"]
    assert c.execute("SELECT COUNT(*) FROM pragma_table_info('papers')"
                     " WHERE name='stale'").fetchone()[0] == 1
    assert store.load_settings(c)["schema_version"] == "2"
