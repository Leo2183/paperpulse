"""视图1:论文列表 —— 筛选、表格、详情面板、右键标记(spec §9)。"""

import json
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)
from PySide6.QtWidgets import QMessageBox

from .. import store

COLUMNS = ["日期", "标题", "期刊", "来源", "标签", "旗标"]


class PaperListView(QWidget):
    def __init__(self, conn, refresh_parent=None):
        super().__init__()
        self.conn = conn
        self.refresh_parent = refresh_parent
        self._build()
        self.reload()

    # ---------------------------------------------------------------- UI

    def _build(self) -> None:
        # 筛选条
        self.f_from = QDateEdit()
        self.f_from.setCalendarPopup(True)
        self.f_to = QDateEdit()
        self.f_to.setCalendarPopup(True)
        self.f_to.setDate(self.f_to.date().currentDate())
        from datetime import date, timedelta

        self.f_from.setDate(self.f_from.date().currentDate().addDays(-30))

        self.f_source = QComboBox()
        self.f_source.addItems(["全部来源", "europepmc", "crossref", "biorxiv", "订阅刊"])
        self.f_status = QComboBox()
        self.f_status.addItems(["全部状态", "unread", "read", "archived"])
        self.f_tag = QComboBox()
        self.f_tag.addItems(["全部标签", "成像/空间", "植物", "注释"])
        self.c_starred = QCheckBox("仅星标")
        self.c_to_read = QCheckBox("仅待精读")
        self.f_title = QLineEdit()
        self.f_title.setPlaceholderText("标题搜索…")
        btn = QPushButton("刷新")
        btn.clicked.connect(self.reload)
        for w, changer in (
            (self.f_source, self.f_source.currentIndexChanged),
            (self.f_status, self.f_status.currentIndexChanged),
            (self.f_tag, self.f_tag.currentIndexChanged),
            (self.c_starred, self.c_starred.stateChanged),
            (self.c_to_read, self.c_to_read.stateChanged),
        ):
            changer.connect(self.reload)
        self.f_title.textChanged.connect(self.reload)

        bar = QHBoxLayout()
        for w in (QLabel("从"), self.f_from, QLabel("到"), self.f_to,
                  self.f_source, self.f_status, self.f_tag,
                  self.c_starred, self.c_to_read, self.f_title, btn):
            bar.addWidget(w)

        # 表格
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        self.table.currentCellChanged.connect(self._show_detail)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)

        # 详情面板
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.btn_open = QPushButton("浏览器打开 DOI")
        self.btn_open.clicked.connect(self._open_doi)
        self.btn_copy = QPushButton("复制 DOI")
        self.btn_copy.clicked.connect(self._copy_doi)
        self.btn_digest = QPushButton("生成导读")
        self.btn_digest.clicked.connect(self._gen_digest)
        detail_bar = QHBoxLayout()
        for b in (self.btn_open, self.btn_copy, self.btn_digest):
            detail_bar.addWidget(b)
        right = QVBoxLayout()
        right.addWidget(QLabel("详情"))
        right.addWidget(self.detail)
        right.addLayout(detail_bar)

        top = QVBoxLayout()
        top.addLayout(bar)
        body = QHBoxLayout()
        body.addWidget(self.table, stretch=3)
        body.addLayout(right, stretch=2)
        top.addLayout(body, stretch=1)
        self.setLayout(top)

    # ---------------------------------------------------------------- 数据

    def _query(self) -> str:
        where, params = [], []
        if self.f_source.currentText() == "订阅刊":
            where.append("subscription IS NOT NULL")
        elif self.f_source.currentText() != "全部来源":
            where.append("source = ?")
            params.append(self.f_source.currentText())
        if self.f_status.currentText() != "全部状态":
            where.append("status = ?")
            params.append(self.f_status.currentText())
        tag = self.f_tag.currentText()
        if tag != "全部标签":
            where.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if self.c_starred.isChecked():
            where.append("starred = 1")
        if self.c_to_read.isChecked():
            where.append("to_read = 1")
        if self.f_title.text().strip():
            where.append("title LIKE ?")
            params.append(f"%{self.f_title.text().strip()}%")
        d_from = self.f_from.date().toString("yyyy-MM-dd")
        d_to = self.f_to.date().toString("yyyy-MM-dd")
        where.append("substr(pub_date,1,10) BETWEEN ? AND ?")
        params.extend([d_from, d_to])
        sql = "SELECT * FROM papers"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY pub_date DESC LIMIT 2000"
        return sql, params

    def reload(self) -> None:
        self.table.setSortingEnabled(False)
        sql, params = self._query()
        rows = [dict(r) for r in self.conn.execute(sql, params).fetchall()]
        self._rows = rows
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            flags = []
            if r["starred"]:
                flags.append("★")
            if r["to_read"]:
                flags.append("待")
            if r["status"] == "read":
                flags.append("读")
            tags = "/".join(json.loads(r["tags"] or "[]"))
            for col, text in enumerate([
                r["pub_date"] or "日期未知", r["title"], r["journal"] or "",
                r["subscription"] or r["source"], tags, " ".join(flags),
            ]):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r["id"])
                self.table.setItem(i, col, item)
        self.table.setRowCount(len(rows))
        self.table.setSortingEnabled(True)

    def _current_row(self) -> dict | None:
        i = self.table.currentRow()
        if 0 <= i < len(self._rows):
            return self._rows[i]
        return None

    def _show_detail(self, *_) -> None:
        r = self._current_row()
        if not r:
            return
        lines = [
            f"## {r['title']}",
            f"{r['authors'] or ''} — *{r['journal'] or ''}* "
            f"({r['pub_date'] or '日期未知'})",
            f"来源: {r['source']}"
            + (f" | 订阅: {r['subscription']}" if r["subscription"] else ""),
            f"标签: {', '.join(json.loads(r['tags'] or '[]')) or '—'}",
            "",
            r["llm_digest"]
            and f"【导读】{r['llm_digest']}"
            or (r["abstract"] or "摘要未提供(Crossref)"),
        ]
        self.detail.setPlainText("\n".join(str(x) for x in lines))

    # ---------------------------------------------------------------- 动作

    def mark_selected(self, field: str, value: int) -> None:
        """右键/快捷标记入口:'status' 用字符串,starred/to_read 用 0/1。"""
        rows = {i.row() for i in self.table.selectedIndexes()} or {
            self.table.currentRow()}
        ids = [self._rows[i]["id"] for i in rows if 0 <= i < len(self._rows)]
        if not ids:
            return
        col = {"status": "status", "starred": "starred", "to_read": "to_read"}[field]
        self.conn.executemany(
            f"UPDATE papers SET {col}=? WHERE id=?",
            [(value, pid) for pid in ids],
        )
        self.conn.commit()
        self.reload()
        if self.refresh_parent:
            self.refresh_parent()

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        toggles = [
            ("标记已读/未读", lambda: self.mark_selected(
                "status", "read" if self._current_row() and
                self._current_row()["status"] != "read" else "unread")),
            ("切换星标 ★", lambda: self.mark_selected(
                "starred", 0 if self._current_row() and
                self._current_row()["starred"] else 1)),
            ("切换待精读", lambda: self.mark_selected(
                "to_read", 0 if self._current_row() and
                self._current_row()["to_read"] else 1)),
            ("归档", lambda: self.mark_selected("status", "archived")),
        ]
        for text, fn in toggles:
            act = QAction(text, self)
            act.triggered.connect(fn)
            menu.addAction(act)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _open_doi(self) -> None:
        r = self._current_row()
        if r and r["url"]:
            webbrowser.open(r["url"])

    def _copy_doi(self) -> None:
        r = self._current_row()
        if r and r["doi"]:
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(r["doi"])

    def _gen_digest(self) -> None:
        r = self._current_row()
        if not r:
            return
        from .. import llm

        settings = store.load_settings(self.conn)
        if not (settings.get("llm_base_url") and settings.get("llm_model")):
            QMessageBox.warning(
                self, "LLM 未配置",
                "请先在「检索式配置」视图中填写 LLM base_url / model / key。")
            return
        cfg = dict(
            llm_base_url=settings["llm_base_url"],
            llm_model=settings["llm_model"],
            llm_api_key=llm.dpapi_decrypt(settings["llm_api_key"])
            if settings.get("llm_api_key") else "",
        )
        from PySide6.QtCore import QThread, Signal

        class _OneDigest(QThread):
            done = Signal(int, int)

            def run(self):
                conn2 = store.connect()
                store.init_db(conn2)
                try:
                    okk, faill = llm.batch_digest(conn2, [dict(r)], cfg)
                finally:
                    conn2.close()
                self.done.emit(okk, faill)

        self._digest_thread = _OneDigest()

        def _fin(ok_n, fail_n):
            self.btn_digest.setEnabled(True)
            self.reload()
            self._show_detail()
            if fail_n:
                QMessageBox.information(
                    self, "生成导读", "生成失败(无摘要或接口错误)。")

        self.btn_digest.setEnabled(False)
        self._digest_thread.done.connect(_fin)
        self._digest_thread.start()
