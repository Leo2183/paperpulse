"""主窗口:左导航四视图 + QStackedWidget(spec §9)。"""

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget, QMainWindow,
    QStackedWidget, QWidget,
)

from .. import store
from .list_view import PaperListView

NAV_LABELS = ["论文列表", "标记管理", "检索式配置", "扫描中心"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("sm-scan — 空间代谢组学论文扫描器")
        self.resize(1280, 800)
        self.conn = store.connect()
        store.init_db(self.conn)

        self.nav = QListWidget()
        self.nav.addItems(NAV_LABELS)
        self.nav.setFixedWidth(140)
        self.nav.currentRowChanged.connect(self._switch)

        self.stack = QStackedWidget()
        self.list_view = PaperListView(self.conn, refresh_parent=self.refresh)
        from .mark_view import MarkView
        from .queries_view import QueriesView

        self.mark_view = MarkView(self.conn, refresh_parent=self.refresh)
        self.queries_view = QueriesView(self.conn, parent_window=self)
        self.views = [self.list_view, self.mark_view, self.queries_view]
        for v in self.views:
            self.stack.addWidget(v)
        # 视图4 在后续任务接入
        for label in NAV_LABELS[3:]:
            placeholder = QLabel(f"{label}(待接入)")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.views.append(placeholder)
            self.stack.addWidget(placeholder)

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

    def set_view(self, index: int, widget: QWidget) -> None:
        """Task 15-17 用真实视图替换占位。"""
        old = self.stack.widget(index)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.stack.insertWidget(index, widget)
        self.views[index] = widget


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()
