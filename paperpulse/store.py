"""SQLite 存储层:六表 schema、迁移、upsert 语义(spec §6)。

时区约定:所有时间戳为本地时区 ISO 字符串。
"""

import json
import logging
import sqlite3
import time
from typing import TypedDict

from . import config

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4

DEFAULT_SETTINGS = {
    "schema_version": "4",
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
  updated_at     TEXT,
  stale          INTEGER DEFAULT 0,
  added_at       TEXT
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
  added_at TEXT,
  mode_user_set INTEGER DEFAULT 0
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
    _sync_journal_modes(conn)  # 每次启动增量同步(v4 起,只动未手动改过的刊)


def _get_schema_version(conn) -> int:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'"
    ).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _migrate(conn, current: int) -> None:
    """按 schema_version 顺序执行迁移(spec §6)。"""
    if current < 2:
        _migrate_v2(conn)
    if current < 3:
        _migrate_v3(conn)
    if current < 4:
        _migrate_v4(conn)


def _migrate_v2(conn) -> None:
    # v2:P1-2 stale 列;P1-3 matched_groups 由组名改为组 id
    cols = {r["name"] for r in conn.execute(
        "SELECT name FROM pragma_table_info('papers')")}
    if "stale" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN stale INTEGER DEFAULT 0")
    id_by_name = {r["name"]: str(r["id"]) for r in conn.execute(
        "SELECT id, name FROM term_groups")}
    if id_by_name:
        rows = conn.execute(
            "SELECT id, matched_groups FROM papers"
        ).fetchall()
        for row in rows:
            try:
                groups = json.loads(row["matched_groups"] or "[]")
            except ValueError:
                continue
            mapped = sorted({id_by_name.get(g, g) for g in groups})
            if mapped != groups:
                conn.execute(
                    "UPDATE papers SET matched_groups=? WHERE id=?",
                    (json.dumps(mapped, ensure_ascii=False), row["id"]))
    conn.commit()


def _migrate_v3(conn) -> None:
    """v3(第11轮 P2-3):内置/preset 订阅的模式修正同步到存量库。

    seed_journals 对非空库是空操作,内置清单的 mode 修正(如 Food
    Chemistry: X full→filter)永远到不了已有库。这里按 ISSN 一次性同步;
    用户手动改过的模式也会被这一次覆盖——改预设主题包的 mode 即为权威。
    延迟 import:journals → queries → 本模块,运行时已加载完毕无环。
    """
    from .sources.journals import get_default_journals

    changed = []
    for r in get_default_journals():
        cur = conn.execute(
            "UPDATE journal_subscriptions SET mode=?"
            " WHERE issn=? AND mode!=?",
            (r["mode"], r["issn"], r["mode"]),
        )
        if cur.rowcount:
            changed.append(f"{r['issn']}->{r['mode']}")
    conn.commit()
    if changed:  # P2-2:v3 无差别同步(含用户手改),必须留痕
        log.info("v3 journal mode sync: %s", ", ".join(changed))


def _migrate_v4(conn) -> None:
    """v4(第12轮 P2-2/P2-4):订阅模式用户标记 + 论文真实入库时刻。

    - journal_subscriptions.mode_user_set:GUI 改过模式的刊置 1,
      此后内置/preset 清单的 mode 修正不再覆盖它(_sync_journal_modes)
    - papers.added_at:真实入库时刻(墙钟)。first_seen 兼任日报归属日
      (回填写当天 00:00),做「新」徽标会把回填论文全部漏掉——徽标
      改用 added_at;迁移前的旧行为 NULL,不标「新」(本就非新)
    """
    cols = {r["name"] for r in conn.execute(
        "SELECT name FROM pragma_table_info('journal_subscriptions')")}
    if "mode_user_set" not in cols:
        conn.execute(
            "ALTER TABLE journal_subscriptions"
            " ADD COLUMN mode_user_set INTEGER DEFAULT 0")
    pcols = {r["name"] for r in conn.execute(
        "SELECT name FROM pragma_table_info('papers')")}
    if "added_at" not in pcols:
        conn.execute("ALTER TABLE papers ADD COLUMN added_at TEXT")
    conn.commit()


def _sync_journal_modes(conn) -> None:
    """每次启动:内置/preset 清单的 mode 修正同步到未手动改过的刊。

    只动 mode_user_set=0 的行(第12轮 P2-2/P3-6,取代"一次性迁移"
    模式——今后预设再改 mode 无需新迁移);用户在 GUI 切换过的刊
    (mode_user_set=1)永不被覆盖。
    """
    from .sources.journals import get_default_journals

    changed = []
    for r in get_default_journals():
        cur = conn.execute(
            "UPDATE journal_subscriptions SET mode=?"
            " WHERE issn=? AND mode!=? AND COALESCE(mode_user_set,0)=0",
            (r["mode"], r["issn"], r["mode"]),
        )
        if cur.rowcount:
            changed.append(f"{r['issn']}->{r['mode']}")
    conn.commit()
    if changed:
        log.info("journal mode sync from preset: %s", ", ".join(changed))


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
        first_seen = p.get("first_seen") or now  # 尊重调用方(run 起始时刻)
        row = conn.execute(
            "SELECT sources, tags, matched_groups, subscription FROM papers WHERE id=?",
            (p["id"],),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO papers (id, doi, pmcid, title, authors, journal,"
                " pub_date, indexed_date, source, sources, subscription, url,"
                " abstract, tags, matched_groups, status, starred, to_read,"
                " llm_digest, first_seen, updated_at, stale, added_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    p["id"], p["doi"], p["pmcid"], p["title"], p["authors"],
                    p["journal"], p["pub_date"], p["indexed_date"], p["source"],
                    json.dumps(p["sources"]), p["subscription"], p["url"],
                    p["abstract"], json.dumps(p["tags"]),
                    json.dumps(p["matched_groups"]), p.get("status", "unread"),
                    p.get("starred", 0), p.get("to_read", 0), p["llm_digest"],
                    first_seen, now,
                    1 if p.get("_stale") else 0,
                    now,  # added_at:真实入库时刻(「新」徽标用,P2-4)
                ),
            )
            conn.commit()
            return True
        old_sources = set(json.loads(row["sources"]))
        old_groups = set(json.loads(row["matched_groups"]))
        sub = row["subscription"] or p["subscription"]  # 首次非空写入
        conn.execute(
            "UPDATE papers SET abstract=COALESCE(?, abstract),"
            " url=COALESCE(?, url), journal=COALESCE(?, journal),"
            " indexed_date=COALESCE(?, indexed_date),"
            " updated_at=?, sources=?,"
            " subscription=?, matched_groups=?,"
            " stale=MAX(COALESCE(stale,0), ?) WHERE id=?",
            (
                p["abstract"], p["url"], p["journal"], p["indexed_date"], now,
                json.dumps(sorted(old_sources | set(p["sources"]))),
                sub,
                json.dumps(sorted(old_groups | set(p["matched_groups"]))),
                1 if p.get("_stale") else 0,
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
        "INSERT INTO term_groups (id, name, enabled, terms, refine)"
        " VALUES (?,?,?,?,?)",
        [
            (r.get("id"), r["name"], int(r.get("enabled", 1)),
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
    def _do() -> None:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()

    _with_lock_retry(_do)  # P3-1:防抖槽/设置页并发写时不再裸抛 locked


def advance_papers_seen(conn) -> str | None:
    """「新」徽标基线:返回上次的 papers_seen_at 旧值,并写入当前时刻。

    GUI 每次启动调用一次——本次会话以旧值为界判定"新增",
    会话内后台扫描新入库的论文持续标「新」,下次打开重新起算。
    首次(无旧值)返回 None:没有参照,不显示「新」。
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key='papers_seen_at'"
    ).fetchone()
    prev = row["value"] if row else None
    save_setting(conn, "papers_seen_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    return prev
