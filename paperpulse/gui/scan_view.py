"""视图4:扫描中心 —— 立即扫描、任务计划、历史报告、批量导读(spec §9)。"""

import logging
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QListWidget, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from .. import config, engine, llm, report, store, task
from . import workers


class _QtLogHandler(logging.Handler):
    def __init__(self, emit_fn):
        super().__init__()
        self._emit = emit_fn
        self.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

    def emit(self, record):
        try:
            self._emit(self.format(record))
        except RuntimeError:  # 组件已销毁
            pass


log = logging.getLogger(__name__)


class ScanWorker(QThread):
    log_line = Signal(str)
    finished_code = Signal(int)

    def run(self):
        from ..logsetup import setup_logging

        setup_logging()  # P2-14:GUI 扫描同样落 scan.log(INFO 级)
        handler = _QtLogHandler(self.log_line.emit)
        root = logging.getLogger()
        root.addHandler(handler)
        rc = 3
        try:
            rc = engine.run_scan()
            try:
                conn = store.connect()
                report.generate(conn, engine.last_report_date(conn))
            except Exception:
                self.log_line.emit("日报生成失败(可稍后 paperpulse report 重试)")
        except Exception as e:  # P2-6:非 SourceError 也必须收尾,防按钮永久禁用
            self.log_line.emit(f"扫描异常终止:{e}")
        finally:
            root.removeHandler(handler)
        self.finished_code.emit(rc)


class DigestWorker(QThread):
    """批量导读线程:conn 线程内自建。"""

    progress = Signal(int, int)
    done = Signal(int, int)

    def __init__(self, papers, cfg):
        super().__init__()
        self.papers, self.cfg = papers, cfg

    def run(self):
        from .. import store as store_mod

        total = len(self.papers)
        done_count = 0

        def on_progress(_):
            nonlocal done_count
            done_count += 1
            self.progress.emit(done_count, total)

        conn = None
        try:
            conn = store_mod.connect()
            store_mod.init_db(conn)
            ok, fail = llm.batch_digest(
                conn, self.papers, self.cfg, progress=on_progress)
        except Exception:  # P2-2:连接/初始化/执行任何异常都必须收尾
            log.exception("batch digest worker aborted")  # P3-1:别让用户查空日志
            ok, fail = 0, -1  # fail<0 标记"异常终止"(区别于单篇失败)
        finally:
            if conn is not None:
                conn.close()
        self.done.emit(ok, fail)


class ScanView(QWidget):
    def __init__(self, conn, refresh_parent=None, llm_cfg_fn=None):
        super().__init__()
        self.conn = conn
        self.refresh_parent = refresh_parent
        self.llm_cfg_fn = llm_cfg_fn
        self._scan_worker = None
        self._digest_worker = None

        self.btn_scan = QPushButton("立即扫描")
        self.btn_scan.clicked.connect(self._start_scan)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # 忙碌态
        self.progress.hide()
        self.log = QTextEdit()
        self.log.setReadOnly(True)

        self.btn_register = QPushButton("注册定时任务(每日 09:00)")
        self.btn_register.clicked.connect(lambda: self._task_action("register"))
        self.btn_remove = QPushButton("删除定时任务")
        self.btn_remove.clicked.connect(lambda: self._task_action("remove"))
        self.task_status = QLabel()

        self.spin_days = QSpinBox()
        self.spin_days.setRange(1, 365)  # 附录 P3-11:库龄>30 天的老论文也要能批量处理
        self.spin_days.setValue(3)
        self.c_regen = QCheckBox("含已有导读(按当前提示词重生成)")
        self.btn_digest = QPushButton("批量生成中文导读")
        self.btn_digest.clicked.connect(self._start_digest)
        self.digest_status = QLabel("")

        self.reports = QListWidget()
        self.reports.itemDoubleClicked.connect(self._open_report)

        top = QHBoxLayout()
        top.addWidget(self.btn_scan)
        top.addWidget(self.progress, stretch=1)
        mid = QHBoxLayout()
        mid.addWidget(QLabel("批量导读:最新"))
        mid.addWidget(self.spin_days)
        mid.addWidget(QLabel("天未读"))
        mid.addWidget(self.c_regen)
        mid.addWidget(self.btn_digest)
        mid.addWidget(self.digest_status, stretch=1)
        task_bar = QHBoxLayout()
        task_bar.addWidget(self.btn_register)
        task_bar.addWidget(self.btn_remove)
        task_bar.addWidget(self.task_status, stretch=1)

        v = QVBoxLayout(self)
        v.addLayout(top)
        v.addWidget(self.log)
        v.addLayout(mid)
        v.addLayout(task_bar)
        v.addWidget(QLabel("历史报告(双击打开):"))
        v.addWidget(self.reports)
        self._reload_reports()
        self._reload_task_status()

    # ------------------------------------------------------------- 扫描

    def _start_scan(self) -> None:
        if self._scan_worker and self._scan_worker.isRunning():
            return
        self.btn_scan.setEnabled(False)
        self.progress.show()
        self.log.append("扫描启动…")
        self._scan_worker = ScanWorker()
        self._scan_worker.log_line.connect(self.log.append)
        self._scan_worker.finished_code.connect(self._scan_done)
        workers.start(self._scan_worker)

    def _scan_done(self, rc: int) -> None:
        self.progress.hide()
        self.btn_scan.setEnabled(True)
        self.log.append(f"扫描结束,退出码 {rc}(0=ok 2=partial 3=failed)")
        self._reload_reports()
        if self.refresh_parent:
            self.refresh_parent()

    # ------------------------------------------------------------- 任务计划

    def _task_action(self, action: str) -> None:
        ok, msg = task.register() if action == "register" else task.remove()
        QMessageBox.information(self, "任务计划", ("OK: " if ok else "FAIL: ") + msg)
        self._reload_task_status()

    def _reload_task_status(self) -> None:
        info = task.status()
        self.task_status.setText(
            "定时任务:已注册(paperpulse-daily,每日 09:00,仅登录时运行)"
            if info["registered"] else "定时任务:未注册")

    # ------------------------------------------------------------- 导读

    def _start_digest(self) -> None:
        if self._digest_worker and self._digest_worker.isRunning():
            return
        cfg = self.llm_cfg_fn() if self.llm_cfg_fn else None
        if not cfg:
            QMessageBox.warning(
                self, "LLM 未配置",
                "请先在「检索式配置 → 源与 LLM」中填写 base_url / model / key。")
            return
        n_days = self.spin_days.value()
        since = (date.today() - timedelta(days=n_days)).isoformat()
        regen = self.c_regen.isChecked()  # 含已有导读:按当前提示词重生成
        where = ["status='unread'", "abstract IS NOT NULL"]
        if not regen:
            where.append("llm_digest IS NULL")
        papers = [dict(r) for r in self.conn.execute(
            "SELECT id, title, abstract, llm_digest FROM papers"
            " WHERE " + " AND ".join(where)
            + " AND substr(first_seen,1,10)>=?",
            (since,))]
        if not papers:
            self.digest_status.setText("没有符合条件的待导读论文。")
            return
        # 附录 P2-5:先亮出规模,大批量(不论是否重生成)统一确认防误触
        self.digest_status.setText(
            f"待生成 {len(papers)} 篇" + ("(含已有导读)" if regen else ""))
        if len(papers) > 50:
            extra = "并重新生成已有导读" if regen else ""
            ans = QMessageBox.question(
                self, "批量导读确认",
                f"将对 {len(papers)} 篇论文{extra}调用 LLM(串行,可能耗时"
                "较久并产生费用),继续?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        if regen:
            # 置 None 让 batch_digest 重调;失败不写库,旧导读保留(P1-3 语义)
            papers = [dict(r, llm_digest=None) for r in papers]
        self.btn_digest.setEnabled(False)
        self._digest_worker = DigestWorker(papers, cfg)
        self._digest_worker.progress.connect(
            lambda d, t: self.digest_status.setText(f"导读中…{d}/{t}"))
        self._digest_worker.done.connect(self._digest_done)
        workers.start(self._digest_worker)

    def _digest_done(self, ok: int, fail: int) -> None:
        self.btn_digest.setEnabled(True)
        self.c_regen.setChecked(False)  # 附录 P3-12:防勾着不放的下轮静默全量重刷
        if fail < 0:  # P2-5:批量异常终止(连接/锁等),区别于单篇失败
            self.digest_status.setText("异常终止")
            QMessageBox.warning(
                self, "批量导读", "批量导读异常终止(部分结果可能已写入,"
                "详见 data/scan.log)。")
        else:
            self.digest_status.setText(f"完成:成功 {ok},失败/跳过 {fail}")
        if self.refresh_parent:
            self.refresh_parent()  # P3-8:异常终止也刷新,部分写入立即可见

    # ------------------------------------------------------------- 报告

    def _reload_reports(self) -> None:
        self.reports.clear()
        for p in sorted(config.reports_dir().glob("*.md"), reverse=True):
            self.reports.addItem(p.name)

    def _open_report(self, item) -> None:
        path: Path = config.reports_dir() / item.text()
        if path.exists():
            import os

            os.startfile(str(path))  # noqa: PTH118  系统默认程序打开
