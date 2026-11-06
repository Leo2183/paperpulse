"""SQLite 存储层:六表 schema、迁移、upsert 语义(spec §6)。

时区约定:所有时间戳为本地时区 ISO 字符串。
"""

import json
import sqlite3
import time
from typing import TypedDict

from . import config

SCHEMA_VERSION = 1

DEFAULT_SETTINGS = {
    "schema_version": "1",
    "llm_base_url": "",
    "llm_api_key": "",  # DPAPI 密文
    "llm_model": "",
    "src_europepmc_enabled": "1",
    "src_journals_enabled": "1",
    "src_biorxiv_enabled": "1",
    "crossref_mailto": "",
    "backfill_days": "30",
}

_JSON_COLS = ("sources", "tags", "matched_groups")


class Paper(TypedDict):
    id: str  # 归一化 DOI 或 titlehash:<hash>
    doi: str | None
    pmcid: str | None
    title: str
    authors: str | None
    journal: str | None
    pub_date: str | None  # YYYY-MM-DD(缺失存年月/年)
    indexed_date: str | None
    source: str  # first_source:europepmc | crossref | biorxiv
    sources: list[str]
    subscription: str | None  # 首次非空写入
    url: str | None
    abstract: str | None
    tags: list[str]  # 首次快照,不随后续更新
    matched_groups: list[str]  # 并集累加
    status: str  # unread | read | archived
    starred: int
    to_read: int
    llm_digest: str | None
    first_seen: str | None
    updated_at: str | None


_DDL = """
CREATE TABLE IF NOT EXISTS papers (
  id             TEXT PRIMARY KEY,
  doi            TEXT,
  pmcid          TEXT,
  title          TEXT NOT NULL,
  authors        TEXT,
  journal        TEXT,
  pub_date       TEXT,
  indexed_date   TEXT,
  source         TEXT NOT NULL,
  sources        TEXT DEFAULT '[]',
  subscription   TEXT,
  url            TEXT,
  abstract       TEXT,
  tags           TEXT DEFAULT '[]',
  matched_groups TEXT DEFAULT '[]',
  status         TEXT DEFAULT 'unread',
  starred        INTEGER DEFAULT 0,
  to_read        INTEGER DEFAULT 0,
  llm_digest     TEXT,
  first_seen     TEXT NOT NULL,
  updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_papers_first_seen ON papers(first_seen);
CREATE INDEX IF NOT EXISTS idx_papers_pub_date ON papers(pub_date);
CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, finished_at TEXT,
  status TEXT,
  new_count INTEGER,
  per_source TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS term_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  terms TEXT NOT NULL,
  refine TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  issn TEXT NOT NULL,
  mode TEXT NOT NULL,
  route TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  added_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(db_path=None) -> sqlite3.Connection:
    path = db_path if db_path is not None else config.data_dir() / "scan.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """幂等:建表 + settings 默认键值 + schema_version 迁移。

    默认检索组与 32 刊清单的 seed 不在此处(归属 queries/journals 模块,
    由引擎首跑时经 seed_groups/seed_journals 写入,避免依赖倒置)。
    """
    conn.executescript(_DDL)
    conn.executemany(
        "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
        DEFAULT_SETTINGS.items(),
    )
    current = _get_schema_version(conn)
    if current < SCHEMA_VERSION:
        _migrate(conn, current)
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()


def _get_schema_version(conn) -> int:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'"
    ).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _migrate(conn, current: int) -> None:
    # 按 schema_version 顺序追加迁移步骤;v1 起步无历史迁移
    pass


def _with_lock_retry(fn, attempts: int = 3, delay: float = 0.2):
    """写事务遇 database is locked 重试(spec §13)。"""
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            last = e
            time.sleep(delay)
    raise last


def upsert_paper(conn: sqlite3.Connection, p: Paper) -> bool:
    """新键 → True(插入);已见 → False(合并更新)。

    已见合并:abstract/url/updated_at 更新;matched_groups 并集累加;
    subscription 首次非空写入;tags 首次快照不动(spec §5/§6)。
    """

    def _do() -> bool:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        row = conn.execute(
            "SELECT sources, tags, matched_groups, subscription FROM papers WHERE id=?",
            (p["id"],),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO papers (id, doi, pmcid, title, authors, journal,"
                " pub_date, indexed_date, source, sources, subscription, url,"
                " abstract, tags, matched_groups, status, starred, to_read,"
                " llm_digest, first_seen, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    p["id"], p["doi"], p["pmcid"], p["title"], p["authors"],
                    p["journal"], p["pub_date"], p["indexed_date"], p["source"],
                    json.dumps(p["sources"]), p["subscription"], p["url"],
                    p["abstract"], json.dumps(p["tags"]),
                    json.dumps(p["matched_groups"]), p.get("status", "unread"),
                    p.get("starred", 0), p.get("to_read", 0), p["llm_digest"],
                    now, now,
                ),
            )
            conn.commit()
            return True
        old_sources = set(json.loads(row["sources"]))
        old_groups = set(json.loads(row["matched_groups"]))
        sub = row["subscription"] or p["subscription"]  # 首次非空写入
        conn.execute(
            "UPDATE papers SET abstract=?, url=?, updated_at=?, sources=?,"
            " subscription=?, matched_groups=? WHERE id=?",
            (
                p["abstract"] or None, p["url"], now,
                json.dumps(sorted(old_sources | set(p["sources"]))),
                sub,
                json.dumps(sorted(old_groups | set(p["matched_groups"]))),
                p["id"],
            ),
        )
        conn.commit()
        return False

    return _with_lock_retry(_do)


def load_groups(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM term_groups ORDER BY id"
    ).fetchall()]


def seed_groups(conn, rows: list[dict]) -> None:
    if conn.execute("SELECT COUNT(*) FROM term_groups").fetchone()[0]:
        return
    conn.executemany(
        "INSERT INTO term_groups (name, enabled, terms, refine) VALUES (?,?,?,?)",
        [
            (r["name"], int(r.get("enabled", 1)),
             json.dumps(r["terms"], ensure_ascii=False),
             json.dumps(r["refine"], ensure_ascii=False))
            for r in rows
        ],
    )
    conn.commit()


def seed_journals(conn, rows: list[dict]) -> None:
    if conn.execute("SELECT COUNT(*) FROM journal_subscriptions").fetchone()[0]:
        return
    conn.executemany(
        "INSERT INTO journal_subscriptions (name, issn, mode, route, enabled, added_at)"
        " VALUES (?,?,?,?,1,?)",
        [
            (r["name"], r["issn"], r["mode"], r.get("route", ""),
             time.strftime("%Y-%m-%dT%H:%M:%S"))
            for r in rows
        ],
    )
    conn.commit()


def load_settings(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def save_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
