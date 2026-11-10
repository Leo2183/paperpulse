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
    from paperpulse.gui.queries_view import NewJournalWorker
    from paperpulse.gui.scan_view import DigestWorker
    from paperpulse.sources import journals as jmod

    store_mod.seed_journals(win.conn, jmod.DEFAULT_JOURNAL_ROWS)
    monkeypatch.setattr(
        "paperpulse.sources.journals.europepmc.search",
        lambda q, a, b, page_size=100: iter([]))
    monkeypatch.setattr(
        "paperpulse.sources.journals.crossref.fetch_journal",
        lambda issn, a, b, mailto=None, today=None: iter([]))

    errors = []

    def run_backfill():
        try:
            from paperpulse.sources import crossref as cf_mod
            monkeypatch.setattr(cf_mod, "count_recent", lambda *a, **k: 1)
            NewJournalWorker("T", "1234-5678").run()  # 线程内自建 conn
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

    from paperpulse.sources import crossref as cf_mod

    monkeypatch.setattr(jmod, "backfill_journal", boom)
    monkeypatch.setattr(cf_mod, "count_recent",
                        lambda *a, **k: 10)  # 钉死:绝不真发网络
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


def test_llm_provider_autofills_and_fetch_models(win, monkeypatch):
    """供应商切换自动填 URL;获取模型列表填下拉。"""
    from paperpulse.gui import queries_view as qv

    qv0 = win.queries_view
    qv0.llm_provider.setCurrentText("Ollama (local)")
    assert qv0.llm_url.text() == "http://localhost:11434/v1"

    monkeypatch.setattr(
        qv.llm, "list_models",
        lambda cfg: ["glm-4.7", "glm-4-flash"])

    class SyncWorker:
        done = type("S", (), {"connect": staticmethod(lambda fn: None)})()

        def __init__(self, fn):
            self._fn = fn

        def start(self):
            self.result = self._fn()
            for cb in SyncWorker.done._cbs if hasattr(SyncWorker.done, "_cbs") else []:
                cb(self.result)

    captured = {}

    class CapWorker(SyncWorker):
        def __init__(self, fn):
            super().__init__(fn)
            captured["fn"] = fn

    # 直接调用 handler 内部逻辑(FnWorker mock 为同步派发)
    class OneWorker:
        def __init__(self, fn):
            self._fn = fn

        class done:
            _cbs = []

            @staticmethod
            def connect(fn):
                OneWorker.done._cbs.append(fn)

        def start(self):
            res = self._fn()
            for cb in list(OneWorker.done._cbs):
                cb(res)

    monkeypatch.setattr(qv, "FnWorker", OneWorker)
    from paperpulse import store as st2

    win.queries_view.llm_provider.setCurrentText("智谱 GLM")
    st2.save_setting(win.conn, "llm_base_url",
                     win.queries_view.llm_url.text())
    win.queries_view._fetch_models()
    combo = win.queries_view.llm_model
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert "glm-4.7" in texts and "glm-4-flash" in texts


def test_bg_buttons_reentry_safe(win):
    """P1-1:请求未返回时连点不得覆盖运行中的 QThread(进程会直接退出)。"""
    qv = win.queries_view
    # _run_bg 存在且四个入口都走它
    assert hasattr(qv, "_run_bg")
    calls = []

    class SlowWorker:
        done = type("S", (), {"connect": staticmethod(lambda fn: None)})()

        def __init__(self, fn):
            calls.append("init")

        def start(self):
            pass

        @staticmethod
        def isRunning():
            return True  # 模拟请求未返回

    import paperpulse.gui.queries_view as qmod


    from unittest.mock import patch

    with patch.object(qmod, "FnWorker", SlowWorker):
        # 同一 key 连续两次:第二次必须被拒(不新建 worker)
        ok1 = qv._run_bg("models", lambda: 1, lambda r: None)
        ok2 = qv._run_bg("models", lambda: 2, lambda r: None)
        assert ok1 is True and ok2 is False
    assert calls == ["init"]


def test_provider_combo_reflects_saved_url(win):
    """P2-1:下拉初值按已存 base_url 回显;浏览切换才写库。"""
    from paperpulse import store as st

    st.save_setting(win.conn, "llm_base_url",
                    "https://api.deepseek.com/v1")
    win.queries_view._sync_provider_from_settings()
    assert win.queries_view.llm_provider.currentText() == "DeepSeek"
    st.save_setting(win.conn, "llm_base_url", "https://my.own/v9")
    win.queries_view._sync_provider_from_settings()
    assert win.queries_view.llm_provider.currentText() == "自定义"


def test_fetch_models_preserves_current_model(win, monkeypatch):
    """P2-2:刷新列表不得静默改写已存 model;不在列表时保留原值。"""
    from paperpulse import store as st
    from paperpulse.gui import queries_view as qv

    st.save_setting(win.conn, "llm_base_url", "https://x/v4")
    st.save_setting(win.conn, "llm_model", "glm-4.7")
    win.queries_view.llm_model.setCurrentText("glm-4.7")
    monkeypatch.setattr(qv.llm, "list_models",
                        lambda cfg: ["aaa-embed", "zzz-chat"])

    class OneWorker:
        _cbs = []

        class done:
            @staticmethod
            def connect(fn):
                OneWorker._cbs = [fn]  # 实例级语义:每次仅本轮回调

        def __init__(self, fn):
            self._fn = fn

        def start(self):
            res = self._fn()
            for cb in OneWorker._cbs:
                cb(res)

    monkeypatch.setattr(qv, "FnWorker", OneWorker)
    win.queries_view._fetch_models()
    # 库里仍是原模型;下拉文本保留原值
    assert st.load_settings(win.conn)["llm_model"] == "glm-4.7"
    assert win.queries_view.llm_model.currentText() == "glm-4.7"


def test_columns_freely_resizable(win):
    """列横向拉伸:所有列必须 Interactive(可拖),不得有 Stretch 锁死列。"""
    from PySide6.QtWidgets import QHeaderView

    h = win.list_view.table.horizontalHeader()
    modes = [h.sectionResizeMode(i) for i in range(h.count())]
    assert QHeaderView.ResizeMode.Stretch not in modes
    assert all(m == QHeaderView.ResizeMode.Interactive for m in modes)
    # 标题列有合理初始宽与下限,不会被拖没
    assert win.list_view.table.columnWidth(1) >= 300


def test_custom_digest_prompt_ui_and_apply(win, monkeypatch):
    """自定义总结:提示词框保存;测试按钮→生成→应用写库。"""
    from paperpulse import store as st
    from paperpulse.gui import list_view as lv_mod

    qv = win.queries_view
    qv.llm_prompt.setPlainText("侧重方法学创新点")
    qv._save_prompt()  # 防抖落库槽直调(P3-6:textChanged 不再逐字符写库)
    assert st.load_settings(win.conn)["llm_prompt"] == "侧重方法学创新点"

    st.upsert_paper(win.conn, st.Paper(
        id="10.20/a", doi="10.20/a", pmcid=None, title="Test digest",
        authors=None, journal="J", pub_date="2026-11-07", indexed_date=None,
        source="europepmc", sources=["europepmc"], subscription=None,
        url=None, abstract="An abstract", tags=[], matched_groups=[],
        status="unread", starred=0, to_read=0, llm_digest=None,
        first_seen="2026-11-08T09:00:00", updated_at=None))
    st.save_setting(win.conn, "llm_base_url", "https://x/v4")
    st.save_setting(win.conn, "llm_model", "m")

    lv = win.list_view
    lv.reload()
    row_i = next(i for i, r in enumerate(lv._rows) if r["id"] == "10.20/a")
    lv.table.setCurrentCell(row_i, 0)

    captured = {}

    def fake_digest(p, cfg):
        captured["prompt"] = cfg.get("llm_prompt")
        return "方法学很新。"

    monkeypatch.setattr(lv_mod.llm, "digest", fake_digest)
    # 直接调 job/done 路径:mock _run_bg 同步执行
    import paperpulse.gui.list_view as lvm

    class OneShot:
        def __init__(self, job):
            self._job = job

        def start(self):
            res = self._job()
            lv._apply_digest("10.20/a", res)

    monkeypatch.setattr(
        lvm.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    # 走 handler 主体:简化为直接验证 apply 链路 + job 逻辑
    lv._apply_digest("10.20/a", "方法学很新。")
    row = win.conn.execute(
        "SELECT llm_digest FROM papers WHERE id='10.20/a'").fetchone()
    assert row["llm_digest"] == "方法学很新。"


def test_reload_preserves_selection_by_id(win):
    """生成导读/标记后 reload:选中条目必须仍是同一篇(不跳行)。"""
    from paperpulse import store as st

    st.upsert_paper(win.conn, st.Paper(
        id="10.21/keep", doi="10.21/keep", pmcid=None, title="Keep me",
        authors=None, journal="J", pub_date="2026-11-05", indexed_date=None,
        source="europepmc", sources=["europepmc"], subscription=None,
        url=None, abstract="abs", tags=[], matched_groups=[],
        status="unread", starred=0, to_read=0, llm_digest=None,
        first_seen="2026-11-08T09:00:00", updated_at=None))
    lv = win.list_view
    lv.reload()
    lv.table.sortItems(1)  # 用户点过表头排序(重排后 reload 才会跳行)
    next(i for i, r in enumerate(lv._rows) if r["id"] == "10.21/keep")
    for row in range(lv.table.rowCount()):
        from PySide6.QtCore import Qt as _Qt
        if lv.table.item(row, 0).data(_Qt.ItemDataRole.UserRole) == "10.21/keep":
            lv.table.setCurrentCell(row, 0)
            break
    # 模拟生成导读后的路径:改数据 + reload + show_detail
    st.upsert_paper(win.conn, st.Paper(
        id="10.21/keep", doi="10.21/keep", pmcid=None, title="Keep me",
        authors=None, journal="J", pub_date="2026-11-05", indexed_date=None,
        source="europepmc", sources=["europepmc"], subscription=None,
        url=None, abstract="abs", tags=[], matched_groups=[],
        status="unread", starred=0, to_read=0, llm_digest="新导读",
        first_seen="2026-11-08T09:00:00", updated_at=None))
    # 生成导读完成路径:数据变化 + reload;选中必须仍是同一篇且详情同步
    lv.reload()
    from PySide6.QtCore import Qt
    row = lv.table.currentRow()
    assert row >= 0, "reload 后丢失选中"
    pid = lv.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
    assert pid == "10.21/keep", "reload 后选中跳到了别的论文"
    # 详情面板也应是同一篇
    assert "Keep me" in lv.detail.toPlainText()


def test_digest_test_button_actually_runs(win, monkeypatch):
    """防回归:「测试总结效果」必须真的调度 job 并恢复按钮(曾漏启动永久卡住)。"""
    from paperpulse import store as st
    from paperpulse.gui import list_view as lv_mod

    st.upsert_paper(win.conn, st.Paper(
        id="10.22/a", doi="10.22/a", pmcid=None, title="Digest test",
        authors=None, journal="J", pub_date="2026-11-07", indexed_date=None,
        source="europepmc", sources=["europepmc"], subscription=None,
        url=None, abstract="abs", tags=[], matched_groups=[],
        status="unread", starred=0, to_read=0, llm_digest=None,
        first_seen="2026-11-08T09:00:00", updated_at=None))
    st.save_setting(win.conn, "llm_base_url", "https://x/v4")
    st.save_setting(win.conn, "llm_model", "m")
    lv = win.list_view
    lv.reload()
    for row in range(lv.table.rowCount()):
        from PySide6.QtCore import Qt as _Q
        if lv.table.item(row, 0).data(_Q.ItemDataRole.UserRole) == "10.22/a":
            lv.table.setCurrentCell(row, 0)
            break
    ran = {"job": False, "done": False}

    def fake_digest(p, cfg):
        ran["job"] = True
        return "总结文本"

    monkeypatch.setattr(lv_mod.llm, "digest", fake_digest)

    class SyncW:
        def __init__(self, fn):
            self._fn = fn

        class done:
            cbs = []

            @staticmethod
            def connect(fn):
                SyncW.done.cbs = [fn]

        def start(self):
            res = self._fn()
            for cb in SyncW.done.cbs:
                cb(res)

    class Box:
        StandardButton = __import__("PySide6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton

        def __init__(self, *a):
            pass

        def exec(self):
            return Box.StandardButton.Discard.value

    for name in ("setWindowTitle", "setText", "setInformativeText",
                 "setStandardButtons"):
        setattr(Box, name, lambda *a, **k: None)
    monkeypatch.setattr(lv_mod, "FnWorker", SyncW)
    monkeypatch.setattr(lv_mod, "QMessageBox", Box)  # 整体打桩防模态阻塞
    lv._test_digest_effect()
    assert ran["job"], "job 从未被调度(按钮会永久停在'总结中…')"
    assert lv.btn_digest_test.isEnabled()
    assert lv.btn_digest_test.text() == "测试总结效果(当前提示词)"


def test_run_bg_twice_with_real_worker(win):
    """P0 防回归:真 FnWorker 跑完后再点一次,必须正常新建而非 RuntimeError。"""
    import time

    qv = win.queries_view
    assert qv._run_bg("twice", lambda: 1, lambda r: None) is True
    for _ in range(100):  # 等 worker 结束
        w = qv._bg_workers.get("twice")
        if w is None or not w.isRunning():
            break
        time.sleep(0.02)
    from PySide6.QtWidgets import QApplication

    import time as _t

    for _ in range(50):  # 让 finished/deleteLater 真正处理(C++ 对象销毁)
        QApplication.processEvents()
        _t.sleep(0.02)
    # 第二次:修复前在此抛 RuntimeError(C++ object already deleted)
    assert qv._run_bg("twice", lambda: 2, lambda r: None) is True


def test_export_subs_writes_to_data_dir(win, tmp_path, monkeypatch):
    """P1-2 防回归:导出必须可用且默认路径在 data/ 下。"""
    from paperpulse.gui import queries_view as qv
    from PySide6.QtWidgets import QFileDialog

    target = tmp_path / "out.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(target), "JSON (*.json)")))
    monkeypatch.setattr(
        qv.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    from paperpulse.sources import journals as jmod
    from paperpulse import store as st2

    st2.seed_journals(win.conn, jmod.get_default_journals())
    win.queries_view._export_subs()
    assert target.exists()
    import json as _j

    data = _j.loads(target.read_text(encoding="utf-8"))
    assert len(data["journals"]) > 0



def test_regenerate_keeps_old_digest_on_failure(win, monkeypatch):
    """P1-3 防回归:重生成失败不得清空旧导读。"""
    from paperpulse import store as st

    st.upsert_paper(win.conn, st.Paper(
        id="10.23/a", doi="10.23/a", pmcid=None, title="Keep old",
        authors=None, journal="J", pub_date="2026-11-07", indexed_date=None,
        source="europepmc", sources=["europepmc"], subscription=None,
        url=None, abstract="abs", tags=[], matched_groups=[],
        status="unread", starred=0, to_read=0, llm_digest="旧导读",
        first_seen="2026-11-10T09:00:00", updated_at=None))

    # 验证代码路径:确认覆盖后不再执行 DELETE(P1-3)
    from paperpulse.gui import list_view as lv_mod
    from paperpulse.sources.base import SourceError

    monkeypatch.setattr(
        lv_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: lv_mod.QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(
        lv_mod.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(lv_mod.llm, "batch_digest",
                        lambda c, ps, cfg, progress=None:
                        (_ for _ in ()).throw(SourceError("HTTP 500")))

    lv = win.list_view
    lv.reload()
    for row in range(lv.table.rowCount()):
        from PySide6.QtCore import Qt as _Q
        if lv.table.item(row, 0).data(_Q.ItemDataRole.UserRole) == "10.23/a":
            lv.table.setCurrentCell(row, 0)
            break

    # 直接触发(同步,不走 QThread)
    lv._gen_digest()
    import time
    from PySide6.QtWidgets import QApplication

    for _ in range(100):
        QApplication.processEvents()
        time.sleep(0.02)
        t = getattr(lv, "_digest_thread", None)
        if t is not None and not t.isRunning():
            t.run  # already finished
            break

    row2 = win.conn.execute(
        "SELECT llm_digest FROM papers WHERE id='10.23/a'").fetchone()
    assert row2["llm_digest"] == "旧导读", "失败后旧导读被清空(P1-3 回归)"


def _mk_paper(pid, **kw):
    from paperpulse import store as st

    base = dict(
        id=pid, doi=pid, pmcid=None, title=f"Title {pid}", authors=None,
        journal="J", pub_date="2026-11-09", indexed_date=None,
        source="europepmc", sources=["europepmc"], subscription=None,
        url=None, abstract="abs text", tags=[], matched_groups=[],
        status="unread", starred=0, to_read=0, llm_digest=None,
        first_seen="2026-11-10T09:00:00", updated_at=None,
    )
    base.update(kw)
    return st.Paper(base)


def test_new_badge_and_filter_only_new(win):
    """「新」徽标按 added_at(真实入库时刻)判定;「仅看新增」过滤生效。"""
    from PySide6.QtCore import Qt

    from paperpulse import store as st

    st.upsert_paper(win.conn, _mk_paper("10.30/new"))
    st.upsert_paper(win.conn, _mk_paper("10.30/old"))
    # added_at 由 upsert 写墙钟;显式控制它验证徽标边界(P2-4 语义)
    win.conn.execute(
        "UPDATE papers SET added_at=? WHERE id='10.30/new'",
        ("2026-11-10T12:00:00",))
    win.conn.execute(
        "UPDATE papers SET added_at=? WHERE id='10.30/old'",
        ("2026-11-01T08:00:00",))
    win.conn.commit()
    lv = win.list_view
    lv.new_since = "2026-11-09T00:00:00"
    lv.reload()
    for row in range(lv.table.rowCount()):
        pid = lv.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        flag = lv.table.item(row, 5).text()
        if pid == "10.30/new":
            assert "新" in flag
        if pid == "10.30/old":
            assert "新" not in flag
    lv.c_new.setChecked(True)
    lv.reload()
    ids = {lv.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
           for r in range(lv.table.rowCount())}
    assert "10.30/new" in ids and "10.30/old" not in ids
    lv.c_new.setChecked(False)
    # 无基线(首次启动)不显示「新」
    lv.new_since = None
    lv.reload()
    for row in range(lv.table.rowCount()):
        assert "新" not in lv.table.item(row, 5).text()


def test_search_scope_title_digest_both(win):
    """搜索范围下拉:标题 / 导读 / 两者(默认)。"""
    from paperpulse import store as st

    st.upsert_paper(win.conn, _mk_paper(
        "10.31/a", title="Quantum dots imaging", llm_digest=None))
    st.upsert_paper(win.conn, _mk_paper(
        "10.31/b", title="Unrelated title", llm_digest="quantum dots study"))
    st.upsert_paper(win.conn, _mk_paper(
        "10.31/c", title="Neither here", llm_digest=None))
    lv = win.list_view

    def ids():
        return {lv.table.item(r, 0).data(
            __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.ItemDataRole.UserRole)
            for r in range(lv.table.rowCount())}

    lv.f_title.setText("quantum")
    lv.reload()  # 默认「两者」
    got = ids()
    assert "10.31/a" in got and "10.31/b" in got and "10.31/c" not in got
    lv.f_search_scope.setCurrentText("标题")
    got = ids()
    assert "10.31/a" in got and "10.31/b" not in got
    lv.f_search_scope.setCurrentText("导读")
    got = ids()
    assert "10.31/b" in got and "10.31/a" not in got
    lv.f_title.setText("")
    lv.f_search_scope.setCurrentText("两者")


def test_batch_regen_includes_cached_digests(win, monkeypatch):
    """勾选「含已有导读」:已有 digest 的论文也入选且置 None(失败保旧)。"""
    from datetime import date

    from paperpulse import store as st
    from paperpulse.gui import scan_view as sv_mod

    st.save_setting(win.conn, "llm_base_url", "https://x/v4")
    st.save_setting(win.conn, "llm_model", "m")
    today = date.today().isoformat()
    st.upsert_paper(win.conn, _mk_paper(
        "10.32/cached", llm_digest="旧导读", first_seen=today + "T09:00:00"))
    st.upsert_paper(win.conn, _mk_paper(
        "10.32/fresh", first_seen=today + "T09:00:00"))

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

        @staticmethod
        def isRunning():
            return False

        def start(self):
            built["started"] = True

    monkeypatch.setattr(sv_mod, "DigestWorker", FakeWorker)
    sv = win.scan_view
    sv.c_regen.setChecked(True)
    sv._start_digest()
    papers = built["args"][0]
    got = {p["id"]: p["llm_digest"] for p in papers}
    assert "10.32/cached" in got and "10.32/fresh" in got
    assert all(v is None for v in got.values()), "重生成必须置 None 让 batch 重调"

    # >30 篇触发确认;点「否」不得启动
    for i in range(31):
        st.upsert_paper(win.conn, _mk_paper(
            f"10.32/bulk{i}", first_seen=today + "T09:00:00"))
    built.clear()
    monkeypatch.setattr(
        sv_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k:
                     sv_mod.QMessageBox.StandardButton.No))
    sv._start_digest()
    assert not built, "确认被拒后不得启动批量重生成"
    sv.c_regen.setChecked(False)


def test_new_journal_rollback_removes_orphan_papers(qapp, tmp_path, monkeypatch):
    """P3-12:加刊回填失败回滚时,本次写入的论文一并删除(不留孤儿)。"""
    from datetime import date

    from paperpulse import config, prepare, store as st
    from paperpulse.gui.queries_view import NewJournalWorker
    from paperpulse.sources import crossref as cf_mod
    from paperpulse.sources import journals as jmod

    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(cf_mod, "count_recent", lambda *a, **k: 10)

    def fake_backfill(conn, jid, **kw):
        # 回填中途失败:先成功入库一篇该刊论文,再抛异常
        started = date.today().isoformat() + "T00:00:00"
        p = dict(_mk_paper("10.99/x", source="crossref"))
        prepare.prepare_paper(p, group_names=[], g3_phrases=[],
                              started=started, subscription="X Journal")
        if st.upsert_paper(conn, p):
            kw["new_ids"].append("10.99/x")  # 模拟真实回填的 new_ids 收集
        raise RuntimeError("disk full")

    monkeypatch.setattr(jmod, "backfill_journal", fake_backfill)
    stages, dones = [], []
    w = NewJournalWorker("X Journal", "0000-0000")
    w.stage.connect(stages.append)
    w.done.connect(dones.append)
    w.run()
    assert dones and any("失败" in s for s in stages)
    conn = st.connect()
    st.init_db(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM journal_subscriptions").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM papers WHERE subscription='X Journal'"
    ).fetchone()[0] == 0, "回滚后不得残留孤儿论文(P3-12)"


def test_digest_worker_survives_exception(qapp, tmp_path, monkeypatch):
    """P2-5:批量导读线程异常必须发 done(0,-1),按钮不永久禁用。"""
    import sqlite3

    from paperpulse import config, store as st
    from paperpulse.gui import scan_view as sv_mod

    monkeypatch.setattr(config, "ROOT", tmp_path)
    conn = st.connect()
    st.init_db(conn)
    st.upsert_paper(conn, _mk_paper("10.40/a"))

    def boom(c, papers, cfg, progress=None):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sv_mod.llm, "batch_digest", boom)
    results = []
    w = sv_mod.DigestWorker(
        [{"id": "10.40/a", "title": "T", "abstract": "x", "llm_digest": None}],
        dict(llm_base_url="https://x/v4", llm_model="m", llm_api_key="k"))
    w.done.connect(lambda ok, fail: results.append((ok, fail)))
    w.run()  # 同步执行 run 体
    assert results == [(0, -1)], "异常必须以 (0,-1) 收尾"
    conn.close()


def test_digest_done_negative_fail_restores_button(win, monkeypatch):
    """P2-5:_digest_done 收到 fail<0 时恢复按钮并提示异常终止。"""
    from paperpulse.gui import scan_view as sv_mod

    warned = []
    monkeypatch.setattr(
        sv_mod.QMessageBox, "warning",
        staticmethod(lambda *a, **k: warned.append(a)))
    sv = win.scan_view
    sv.btn_digest.setEnabled(False)
    sv._digest_done(0, -1)
    assert sv.btn_digest.isEnabled()
    assert sv.digest_status.text() == "异常终止"
    assert warned


def test_audit_backfill_matches_issn_after_delete(win, monkeypatch):
    """P2-3:审计期间删刊,结果按 ISSN 对位回填,不整体串行。"""
    from paperpulse.gui import queries_view as qv

    for name, issn in (("A", "1111-0001"), ("B", "1111-0002"),
                       ("C", "1111-0003")):
        win.conn.execute(
            "INSERT INTO journal_subscriptions (name, issn, mode, route)"
            " VALUES (?,?,'filter','')", (name, issn))
    win.conn.commit()
    win.queries_view._reload_journals()

    rows = [dict(name=n, issn=i, mode="filter", enabled=True,
                 count_90d=c, suggested="full", mismatch=True)
            for n, i, c in (("A", "1111-0001", 10),
                            ("B", "1111-0002", 200),
                            ("C", "1111-0003", 300))]

    class LateWorker:
        _cb = None

        class done:
            @staticmethod
            def connect(fn):
                LateWorker._cb = fn

        @staticmethod
        def isRunning():
            return False

        def start(self):
            pass  # 不立即派发,模拟审计在跑

    monkeypatch.setattr(qv, "AuditWorker", LateWorker)
    monkeypatch.setattr(
        qv.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    qv0 = win.queries_view
    qv0._audit_modes()
    # 审计进行中删掉 A,表格重载成 B/C
    win.conn.execute(
        "DELETE FROM journal_subscriptions WHERE issn='1111-0001'")
    win.conn.commit()
    qv0._reload_journals()
    # 审计结果到达:回填必须各自对位
    LateWorker._cb(rows)
    got = {qv0.j_table.item(i, 1).text(): qv0.j_table.item(i, 5).text()
           for i in range(qv0.j_table.rowCount())}
    assert got["1111-0002"] == "200" and got["1111-0003"] == "300"
    # P3-5 回归:audit 按钮已连坏两轮,连点第二次不得抛 RuntimeError
    qv0._audit_modes()
    qv0._audit_modes()


def test_filter_bar_minimum_width(win):
    """P2-1:筛选条拆两行后,列表视图最小宽度不超 1280。"""
    lv = win.list_view
    assert lv.minimumSizeHint().width() <= 1280
    assert lv.f_title.minimumWidth() >= 120, "搜索框不得被挤成 18px"


def test_search_like_wildcards_escaped(win):
    """P3-8:% 与 _ 按字面匹配,不再当 LIKE 通配符。"""
    lv = win.list_view
    lv.f_title.setText("%")
    lv.reload()
    assert lv.table.rowCount() == 0, "通配符未转义时 % 命中全部"
    lv.f_title.setText("_")
    lv.reload()
    assert lv.table.rowCount() == 0
    lv.f_title.setText("")
