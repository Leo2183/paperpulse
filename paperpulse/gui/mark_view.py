"""视图2:标记管理 —— 复用视图1 表格组件,补交叉筛选/批量操作/批量导出(spec §9)。"""

from PySide6.QtWidgets import QHBoxLayout, QPushButton

from .. import config, report
from .list_view import PaperListView


class MarkView(PaperListView):
    """交叉筛选(状态×星标×待精读×标签×日期)由基类筛选条提供;
    批量标记由基类 mark_selected(选中行集合)提供;本类加批量导出。"""

    def _build(self) -> None:
        super()._build()
        bar = QHBoxLayout()
        btn_todo = QPushButton("导出待精读清单")
        btn_todo.clicked.connect(self._export_todo)
        btn_current = QPushButton("导出当前筛选结果")
        btn_current.clicked.connect(self._export_current)
        bar.addWidget(btn_todo)
        bar.addWidget(btn_current)
        # 挂到布局末尾
        self.layout().addLayout(bar)

    def _export_todo(self) -> None:
        path = report.export_todo(self.conn)
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(self, "导出完成", f"已写入 {path}")

    def _export_current(self) -> None:
        from datetime import date

        from PySide6.QtWidgets import QMessageBox

        lines = [f"# 当前筛选结果 {date.today().isoformat()}({len(self._rows)} 篇)", ""]
        for r in self._rows:
            digest = f" — {r['llm_digest']}" if r["llm_digest"] else ""
            link = r["url"] or (
                f"https://doi.org/{r['doi']}" if r["doi"] else "")
            item = f"- [{r['title']}]({link})" if link else f"- {r['title']}"
            lines.append(item + digest)
        lines.append("")
        path = config.reports_dir() / (
            f"filtered-{date.today().isoformat()}.md")
        path.write_text("\n".join(lines), encoding="utf-8")
        QMessageBox.information(self, "导出完成", f"已写入 {path}")
