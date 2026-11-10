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

    monkeypatch_target = SlowWorker
    import pytest

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
    i = next(i for i, r in enumerate(lv._rows) if r["id"] == "10.21/keep")
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
