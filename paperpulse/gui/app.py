"""主窗口:左导航四视图 + QStackedWidget(spec §9)。"""

import sys

from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QListWidget, QMainWindow,
    QStackedWidget, QWidget,
)

from .. import store
from .list_view import PaperListView

NAV_LABELS = ["论文列表", "标记管理", "检索式配置", "扫描中心"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("paperpulse — 论文监控器")
        self.resize(1280, 800)
        self.conn = store.connect()
        store.init_db(self.conn)
        # 「新」徽标基线:读上次会话时刻并推进到当前(本次会话内以此判定新增)
        self.new_since = store.advance_papers_seen(self.conn)

        self.nav = QListWidget()
        self.nav.addItems(NAV_LABELS)
        self.nav.setFixedWidth(140)
        self.nav.currentRowChanged.connect(self._switch)

        self.stack = QStackedWidget()
        self.list_view = PaperListView(
            self.conn, refresh_parent=self.refresh, new_since=self.new_since)
        from .mark_view import MarkView
        from .queries_view import QueriesView
        from .scan_view import ScanView

        self.mark_view = MarkView(
            self.conn, refresh_parent=self.refresh, new_since=self.new_since)
        self.queries_view = QueriesView(self.conn, parent_window=self)
        self.scan_view = ScanView(
            self.conn, refresh_parent=self.refresh,
            llm_cfg_fn=self.queries_view._llm_cfg)
        self.views = [self.list_view, self.mark_view,
                      self.queries_view, self.scan_view]
        for v in self.views:
            self.stack.addWidget(v)

        layout = QHBoxLayout()
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, stretch=1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.nav.setCurrentRow(0)

    def _switch(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        view = self.views[row]
        if hasattr(view, "reload"):
            view.reload()

    def refresh(self) -> None:
        """扫描/标记变化后刷新当前视图。"""
        view = self.stack.currentWidget()
        if hasattr(view, "reload"):
            view.reload()

    def closeEvent(self, event) -> None:
        """收尾:防抖中的提示词落库 + 后台线程仍在跑时确认(第12轮 P3-1/P3-11)。"""
        try:
            self.queries_view._save_prompt()  # 防抖窗口内的最后编辑不丢
        except Exception:
            pass
        busy = [w for w in (getattr(self.scan_view, "_scan_worker", None),
                            getattr(self.scan_view, "_digest_worker", None))
                if w is not None and w.isRunning()]
        if busy:
            from PySide6.QtWidgets import QMessageBox

            ans = QMessageBox.question(
                self, "退出确认",
                "后台扫描/批量导读仍在进行,退出会中断并丢失部分进度。确定退出?")
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()
