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
    from sm_scan import config, store

    monkeypatch.setattr(config, "ROOT", tmp_path)
    from sm_scan.gui.app import MainWindow

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

    from sm_scan import report

    win.conn.execute("UPDATE papers SET to_read=1 WHERE id='10.1/a'")
    win.conn.commit()
    path = report.export_todo(win.conn, date(2026, 11, 6))
    text = path.read_text(encoding="utf-8")
    assert "Plant MALDI imaging" in text
    assert "Nature pick" not in text


def test_queries_view_group3_refine_guard(win):
    from sm_scan import queries as queries_mod
    from sm_scan import store as store_mod

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
    from sm_scan import llm as llm_mod

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
