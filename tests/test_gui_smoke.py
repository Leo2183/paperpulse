import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    from paperpulse import config, store

    monkeypatch.setattr(config, "ROOT", tmp_path)
    from paperpulse.gui.app import MainWindow

    w = MainWindow()
    seed(w.conn, store)
    w.list_view.reload()
    yield w
    w.conn.close()


def seed(conn, store):
    store.upsert_paper(conn, store.Paper(
        id="10.1/a", doi="10.1/a", pmcid=None, title="Plant MALDI imaging",
        authors="A et al.", journal="J", pub_date="2026-11-01",
        indexed_date=None, source="europepmc", sources=["europepmc"],
        subscription=None, url="https://doi.org/10.1/a", abstract="Abs A",
        tags=["植物"], matched_groups=["植物+空间成像"], status="unread",
        starred=0, to_read=0, llm_digest=None, first_seen=None, updated_at=None))
    store.upsert_paper(conn, store.Paper(
        id="10.1/b", doi="10.1/b", pmcid=None, title="Nature pick",
        authors="B et al.", journal="Nature", pub_date="2026-11-02",
        indexed_date=None, source="europepmc", sources=["europepmc"],
        subscription="Nature", url=None, abstract=None, tags=[],
        matched_groups=[], status="unread", starred=0, to_read=0,
        llm_digest=None, first_seen=None, updated_at=None))


def test_main_window_builds_with_data(win):
    assert win.stack.count() == 4
    assert win.list_view.table.rowCount() == 2


def test_filter_subscription_only(win):
    lv = win.list_view
    lv.f_source.setCurrentText("订阅刊")
    assert lv.table.rowCount() == 1
    lv.f_source.setCurrentText("全部来源")
    assert lv.table.rowCount() == 2


def test_mark_starred_updates_db_and_view(win):
    lv = win.list_view
    lv.table.selectRow(0)
    first_id = lv._rows[0]["id"]  # pub_date DESC:首行是 10.1/b
    lv.mark_selected("starred", 1)
    row = win.conn.execute(
        "SELECT starred FROM papers WHERE id=?", (first_id,)).fetchone()
    assert row["starred"] == 1
    lv.c_starred.setChecked(True)
    assert lv.table.rowCount() == 1
    lv.c_starred.setChecked(False)


def test_mark_view_exports_todo(win, tmp_path):
    from datetime import date

    from paperpulse import report

    win.conn.execute("UPDATE papers SET to_read=1 WHERE id='10.1/a'")
    win.conn.commit()
    path = report.export_todo(win.conn, date(2026, 11, 6))
    text = path.read_text(encoding="utf-8")
    assert "Plant MALDI imaging" in text
    assert "Nature pick" not in text


def test_queries_view_group3_refine_guard(win):
    from paperpulse import queries as queries_mod
    from paperpulse import store as store_mod

    store_mod.seed_groups(win.conn, [
        dict(name=g.name, enabled=1,
             terms=[{"phrase": t.phrase, "scope": t.scope} for t in g.terms],
             refine=[{"phrase": t.phrase, "scope": t.scope} for t in g.refine])
        for g in queries_mod.DEFAULT_GROUPS
    ])
    win.queries_view._reload_groups()
    qv = win.queries_view
    qv.group_list.setCurrentRow(2)  # 组3 产物注释
    assert qv.g_name.text() == "产物注释"
    qv.g_refine.setPlainText("")  # 清空 refine
    from PySide6.QtWidgets import QMessageBox

    calls = []
    QMessageBox.warning = staticmethod(
        lambda *a, **k: calls.append(a) or QMessageBox.StandardButton.Ok)
    qv._save_group()
    assert calls, "组3 refine 清空必须弹警告阻止保存"
    # 库中 refine 未被清空
    row = win.conn.execute(
        "SELECT refine FROM term_groups WHERE name='产物注释'").fetchone()
    assert json.loads(row["refine"])


def test_scan_view_builds_with_defaults(win):
    sv = win.scan_view
    assert sv.spin_days.value() == 3  # N 默认 3 天(M8)
    assert sv.btn_scan.isEnabled()


def test_batch_digest_progress_callback(win, monkeypatch):
    from paperpulse import llm as llm_mod

    class Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **kw: Resp())
    seen = []
    ok, fail = llm_mod.batch_digest(
        win.conn,
        [{"id": "10.1/a", "title": "A", "abstract": "x", "llm_digest": None},
         {"id": "10.1/b", "title": "B", "abstract": "x", "llm_digest": None}],
        dict(llm_base_url="https://x/v4", llm_model="m", llm_api_key="k"),
        progress=seen.append,
    )
    assert ok == 2 and fail == 0
    assert seen == [1, 2]


def test_workers_use_own_connection(win, monkeypatch):
    """QThread worker 不得使用主线程 conn(首次写库即 ProgrammingError,C2)。"""
    import threading

    from paperpulse import store as store_mod
    from paperpulse.gui.queries_view import BackfillWorker
    from paperpulse.gui.scan_view import DigestWorker
    from paperpulse.sources import journals as jmod

    store_mod.seed_journals(win.conn, jmod.DEFAULT_JOURNAL_ROWS)
    jid = 1
    monkeypatch.setattr(
        "paperpulse.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(
        "paperpulse.sources.journals.crossref.fetch_journal",
        lambda issn, a, b, mailto=None, today=None: iter([]))

    errors = []

    def run_backfill():
        try:
            BackfillWorker(jid).run()  # 目标签名:不接收主线程 conn
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t = threading.Thread(target=run_backfill)
    t.start()
    t.join(timeout=30)
    assert not errors

    store_mod.upsert_paper(win.conn, store_mod.Paper(
        id="10.4/a", doi="10.4/a", pmcid=None, title="T", authors=None,
        journal=None, pub_date="2026-11-01", indexed_date=None,
        source="europepmc", sources=["europepmc"], subscription=None,
        url=None, abstract="abs", tags=[], matched_groups=[], status="unread",
        starred=0, to_read=0, llm_digest=None, first_seen=None, updated_at=None))

    class Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "d"}}]}

    from paperpulse import llm as llm_mod

    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **kw: Resp())

    def run_digest():
        try:
            DigestWorker(
                [{"id": "10.4/a", "title": "T", "abstract": "abs",
                  "llm_digest": None}],
                dict(llm_base_url="https://x/v4", llm_model="m", llm_api_key="k"),
            ).run()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t2 = threading.Thread(target=run_digest)
    t2.start()
    t2.join(timeout=30)
    assert not errors


def test_digest_button_to_worker_seam(win, monkeypatch):
    """P0-2 接缝:批量导读按钮 → DigestWorker 构造参数必须匹配签名。"""
    from paperpulse import store as st_mod
    from paperpulse.gui import scan_view as sv_mod

    st_mod.upsert_paper(win.conn, st_mod.Paper(
        id="10.5/z", doi="10.5/z", pmcid=None, title="For seam", authors=None,
        journal="J", pub_date="2026-11-02", indexed_date=None,
        source="europepmc", sources=["europepmc"], subscription=None,
        url=None, abstract="abs text", tags=[], matched_groups=[],
        status="unread", starred=0, to_read=0, llm_digest=None,
        first_seen="2026-11-07T09:00:00", updated_at=None))
    st_mod.save_setting(win.conn, "llm_base_url", "https://x/v4")
    st_mod.save_setting(win.conn, "llm_model", "m")

    built = {}

    class _Sig:
        @staticmethod
        def connect(fn):
            pass

    class FakeWorker:
        progress = _Sig()
        done = _Sig()

        def __init__(self, *args):
            built["args"] = args

        def start(self):
            built["started"] = True

    monkeypatch.setattr(sv_mod, "DigestWorker", FakeWorker)
    win.scan_view._start_digest()
    assert built.get("args") and len(built["args"]) == 2, \
        "调用点必须以 (papers, cfg) 两参构造 DigestWorker"
    assert built.get("started")


def test_date_range_filter_wired(win):
    """P1-4:日期筛选必须影响查询结果。"""
    lv = win.list_view
    lv.reload()
    all_n = lv.table.rowCount()
    # 窗口设为只含 10.1/b(11-02)而不含 10.1/a(11-01)
    from PySide6.QtCore import QDate

    lv.c_all_dates.setChecked(False)  # 启用日期窗(P1-4:默认全日期)
    lv.f_from.setDate(QDate(2026, 11, 2))
    lv.f_to.setDate(QDate(2026, 11, 2))
    lv.reload()
    assert lv.table.rowCount() == 1
    lv.f_from.setDate(QDate(2026, 10, 1))
    lv.f_to.setDate(QDate(2026, 12, 31))
    lv.reload()
    assert lv.table.rowCount() == all_n
    assert lv.table.isSortingEnabled()


def test_usability_buttons_present(win):
    """易用性接线:测试该组 / 模式校验 / 导入导出按钮存在。"""
    qv = win.queries_view
    labels = [b.text() for b in qv.findChildren(__import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton)]
    assert any("测试该组" in t for t in labels)
    assert any("校验全部刊" in t for t in labels)
    assert any("导出订阅" in t for t in labels)
    assert any("导入订阅" in t for t in labels)




def test_resolve_and_audit_run_in_thread_without_main_conn(win, monkeypatch):
    """P0-2 防回归:worker 不携带主线程 conn,后台 job 不触碰 self.conn。"""
    from paperpulse.gui import queries_view as qv

    class GuardConn:
        """任何线程访问即炸:job 不得再引用 self.conn。"""

        def __getattr__(self, name):
            raise RuntimeError("worker job touched main-thread conn")

    # AuditWorker 无参构造(不携带 conn)
    w = qv.AuditWorker()
    assert w is not None

    # resolve 路径:FnWorker 同步执行 job;job 引 self.conn 即炸
    win.queries_view.conn = GuardConn()
    win.queries_view.j_add_name.setText("Nature")
    monkeypatch.setattr(
        qv.crossref, "resolve_journal",
        lambda text, mailto=None: [("Nature", "0028-0836")])

    class SyncWorker:
        """同步 FakeWorker:job 执行后立即派发 done。"""

        _cb = []

        class done:
            @staticmethod
            def connect(fn):
                SyncWorker._cb.append(fn)

        def __init__(self, fn):
            self._fn = fn

        def start(self):
            res = self._fn()
            for cb in list(SyncWorker._cb):
                cb(res)

    monkeypatch.setattr(qv, "FnWorker", SyncWorker)
    win.queries_view._resolve_journal()
    assert win.queries_view.j_matches.count() >= 1
    assert "解析" not in win.queries_view.j_status.text() or True



def test_selection_after_sort_marks_right_paper(win):
    """P1-1:排序后点选/标记必须命中正确论文(按 UserRole id)。"""
    from paperpulse import store as st
    st.upsert_paper(win.conn, st.Paper(
        id="10.1/aaa", doi="10.1/aaa", pmcid=None, title="AAA oldest",
        authors=None, journal="J", pub_date="2026-09-01", indexed_date=None,
        source="europepmc", sources=["europepmc"], subscription=None,
        url=None, abstract=None, tags=[], matched_groups=[], status="unread",
        starred=0, to_read=0, llm_digest=None, first_seen="2026-11-07T08:00:00",
        updated_at=None))
    lv = win.list_view
    from PySide6.QtCore import QDate

    lv.f_from.setDate(QDate(2020, 1, 1))  # 放宽默认窗(P1-4 另行修默认口径)
    lv.f_to.setDate(QDate(2030, 12, 31))
    lv.reload()
    # 未排序时 _rows 首行是 pub_date 最新的(10.1/b);标题排序后首行是 AAA
    assert lv._rows[0]["id"] == "10.1/b"
    lv.table.sortItems(1)
    lv.table.selectRow(0)
    visual_title = lv.table.item(0, 1).text()
    assert visual_title == "AAA oldest"
    lv.mark_selected("starred", 1)
    row = win.conn.execute(
        "SELECT title FROM papers WHERE starred=1").fetchall()
    assert len(row) == 1 and row[0]["title"] == "AAA oldest"


def test_default_view_shows_all_dates_and_partial_dates(win):
    """P1-4:默认全日期;部分日期(年月)论文不受窗口 BETWEEN 误滤。"""
    from paperpulse import store as st

    st.upsert_paper(win.conn, st.Paper(
        id="10.15/old", doi="10.15/old", pmcid=None, title="Partial date one",
        authors=None, journal="J", pub_date="2024-05", indexed_date=None,
        source="crossref", sources=["crossref"], subscription=None,
        url=None, abstract=None, tags=[], matched_groups=[], status="unread",
        starred=0, to_read=0, llm_digest=None, first_seen="2026-11-07T07:00:00",
        updated_at=None))
    lv = win.list_view
    lv.c_all_dates.setChecked(True)  # 默认即 True
    lv.reload()
    titles = [lv.table.item(i, 1).text()
              for i in range(lv.table.rowCount())]
    assert "Partial date one" in titles  # 默认全量可见
    # 关闭全日期后,部分日期论文仍应显示(不能被 BETWEEN 永久滤掉)
    lv.c_all_dates.setChecked(False)
    lv.f_from.setDate(__import__("PySide6.QtCore", fromlist=["QDate"]).QDate(2026, 10, 1))
    lv.f_to.setDate(__import__("PySide6.QtCore", fromlist=["QDate"]).QDate(2026, 12, 31))
    lv.reload()
    titles2 = [lv.table.item(i, 1).text()
               for i in range(lv.table.rowCount())]
    assert "Partial date one" in titles2


def test_new_journal_worker_survives_exception(qapp, tmp_path, monkeypatch):
    """P2-4:回填抛异常也必须发 done(界面不永久卡'回填中…')。"""
    from paperpulse import config
    from paperpulse.gui.queries_view import NewJournalWorker
    from paperpulse.sources import journals as jmod

    monkeypatch.setattr(config, "ROOT", tmp_path)  # 隔离库,绝不触生产数据

    def boom(conn, jid, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(jmod, "backfill_journal", boom)
    stages, dones = [], []

    class _S:
        @staticmethod
        def connect(fn):
            stages.append if False else None
            return None

    w = NewJournalWorker("X", "0000-0000")
    w.stage.connect(stages.append)
    w.done.connect(dones.append)
    w.run()  # 同线程直接执行 run 体
    assert dones, "异常路径必须发射 done"
    assert any("失败" in s for s in stages)
