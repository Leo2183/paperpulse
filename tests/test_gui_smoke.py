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
