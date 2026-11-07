"""视图3:检索式配置 —— 检索组 / 订阅期刊 / 源与 LLM 三个页签(spec §9)。

短语行内语法:每行一条;以 `|t` 结尾表示 scope=title(歧义缩写仅标题)。
"""

import json

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
    QWidget,
)

from .. import llm, queries, store
from ..sources import crossref, journals


class FnWorker(QThread):
    """通用后台函数执行;done 后自毁,持有者经 _run_bg 防重入(P1-1/P2-8)。"""

    done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.finished.connect(self.deleteLater)

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:  # noqa: BLE001
            self.done.emit(e)


class TestGroupWorker(QThread):
    """「测试该组」:近 7 天真实请求,预览命中(易用性)。"""

    done = Signal(object)  # (ok, payload)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        from datetime import date, timedelta

        from ..sources import europepmc
        from ..sources.base import SourceError

        to = date.today()
        fr = to - timedelta(days=7)
        try:
            hits = []
            for p in europepmc.search(self.query, fr, to):
                hits.append(p["title"])
                if len(hits) >= 5:
                    break
            self.done.emit(True, hits)
        except SourceError as e:
            self.done.emit(False, str(e))


class AuditWorker(QThread):
    """「校验全部刊模式」:后台跑 audit_modes,完成回报行列表。"""

    done = Signal(object)

    def __init__(self):
        super().__init__()

    def run(self):
        from .. import store as store_mod

        conn = store_mod.connect()
        store_mod.init_db(conn)
        try:
            self.done.emit(journals.audit_modes(conn))
        except Exception as e:  # noqa: BLE001
            self.done.emit(e)
        finally:
            conn.close()


class NewJournalWorker(QThread):
    """加刊全链后台(P0-2/P2-8):count_recent + 入库 + 90 天回填,自建连接。"""

    stage = Signal(str)
    progress = Signal(int)
    done = Signal(int)

    def __init__(self, title: str, issn: str):
        super().__init__()
        self.title, self.issn = title, issn

    def run(self):
        from .. import store as store_mod

        conn = store_mod.connect()
        store_mod.init_db(conn)
        try:
            n = self._run_inner(conn)
        except Exception as e:  # P2-4:失败必须收尾,界面不得永久卡住
            try:
                self.stage.emit(f"添加/回填失败:{e};已撤销订阅行,可重试")
            except RuntimeError:
                pass
            jid = getattr(self, "_inserted_jid", None)
            if jid is not None:  # P2-3:删半成品订阅,重复添加可重试
                try:
                    conn.execute(
                        "DELETE FROM journal_subscriptions WHERE id=?", (jid,))
                    conn.commit()
                except Exception:
                    pass
                self._inserted_jid = None
            n = 0
        finally:
            conn.close()
            self.done.emit(n)

    def _run_inner(self, conn) -> int:
        """探测→入库→回填;异常由 run() 统一收尾(P2-4)。"""
        from .. import store as store_mod

        settings = store_mod.load_settings(conn)
        dup = conn.execute(
            "SELECT id FROM journal_subscriptions WHERE issn=?",
            (self.issn,)).fetchone()
        if dup:
            self.stage.emit(f"{self.title} 已在订阅列表中,跳过重复添加")
            return 0
        self.stage.emit(f"探测 {self.title} 近 90 天发文量…")
        try:
            count = crossref.count_recent(
                self.issn, mailto=settings.get("crossref_mailto") or None)
        except Exception:
            count = -1
        mode = journals.suggest_mode(count) if count >= 0 else "filter"
        cur = conn.execute(
            "INSERT INTO journal_subscriptions (name, issn, mode, route,"
            " enabled, added_at) VALUES (?,?,?,'',1,datetime('now','localtime'))",
            (self.title, self.issn, mode))
        conn.commit()
        jid = cur.lastrowid
        self._inserted_jid = jid  # 失败时由 run() 回滚(P2-3)
        extra = "" if count >= 0 else "(探测失败,暂按 filter)"
        self.stage.emit(
            f"已添加 {self.title}:90 天 "
            f"{count if count >= 0 else '?'} 条,模式 {mode}{extra};回填中…")
        n = journals.backfill_journal(
            conn, jid, progress=self.progress.emit,
            should_stop=self.isInterruptionRequested,
            mailto=settings.get("crossref_mailto") or None)
        return n


def _parse_terms(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith("|t"):
            out.append({"phrase": line[:-2].strip(), "scope": "title"})
        else:
            out.append({"phrase": line, "scope": "title_abs"})
    return out


def _terms_text(rows: list[dict]) -> str:
    return "\n".join(
        t["phrase"] + ("|t" if t.get("scope") == "title" else "")
        if isinstance(t, dict) else str(t)
        for t in rows
    )


class QueriesView(QWidget):
    def _run_bg(self, key: str, job, done_cb) -> bool:
        """统一后台入口:同 key 运行中拒绝重入;done 后线程自毁(P1-1)。"""
        w = self._bg_workers.get(key)
        if w is not None and w.isRunning():
            return False
        worker = FnWorker(job)
        worker.done.connect(done_cb)
        self._bg_workers[key] = worker
        worker.start()
        return True

    def __init__(self, conn, parent_window: QMainWindow | None = None):
        super().__init__()
        self.conn = conn
        self.win = parent_window
        self._worker = None
        self._bg_workers: dict = {}
        tabs = QTabWidget()
        tabs.addTab(self._build_groups_page(), "检索组")
        tabs.addTab(self._build_journals_page(), "订阅期刊")
        tabs.addTab(self._build_settings_page(), "源与 LLM")
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)

    # ------------------------------------------------------------- 检索组

    def _build_groups_page(self) -> QWidget:
        self.group_list = QListWidget()
        self.group_list.currentRowChanged.connect(self._load_group)
        self.g_name = QLineEdit()
        self.g_enabled = QCheckBox("启用")
        self.g_terms = QTextEdit()
        self.g_terms.setPlaceholderText("每行一条短语;行尾加 |t 表示仅标题匹配")
        self.g_refine = QTextEdit()
        self.g_refine.setPlaceholderText("组内 AND 限定词,每行一条(组3 必填)")
        save = QPushButton("保存该组")
        save.clicked.connect(self._save_group)
        test_g = QPushButton("测试该组(近 7 天真实命中)")
        test_g.clicked.connect(self._test_group)
        form = QFormLayout()
        form.addRow("组名", self.g_name)
        form.addRow("", self.g_enabled)
        form.addRow("terms", self.g_terms)
        form.addRow("refine", self.g_refine)
        form.addRow("", save)
        form.addRow("", test_g)
        self.g_status = QLabel("")
        form.addRow("", self.g_status)
        page = QWidget()
        h = QHBoxLayout(page)
        h.addWidget(self.group_list)
        box = QWidget()
        box.setLayout(form)
        h.addWidget(box, stretch=1)
        self._reload_groups()
        return page

    def _reload_groups(self) -> None:
        self.group_list.clear()
        for r in store.load_groups(self.conn):
            self.group_list.addItem(
                f"{'✓' if r['enabled'] else '✗'} {r['name']}")

    def _load_group(self, row: int) -> None:
        groups = store.load_groups(self.conn)
        if not (0 <= row < len(groups)):
            return
        g = groups[row]
        self._gid = g["id"]
        self.g_name.setText(g["name"])
        self.g_enabled.setChecked(bool(g["enabled"]))
        self.g_terms.setPlainText(_terms_text(json.loads(g["terms"])))
        self.g_refine.setPlainText(_terms_text(json.loads(g["refine"])))

    def _save_group(self) -> None:
        groups = store.load_groups(self.conn)
        row = self.group_list.currentRow()
        if not (0 <= row < len(groups)):
            return
        gid = groups[row]["id"]
        name = self.g_name.text().strip()
        refine = _parse_terms(self.g_refine.toPlainText())
        from .. import queries as _q

        roles = _q.group_roles(self.conn)
        ann = roles["annotation"]
        gid = groups[row]["id"] if 0 <= row < len(groups) else None
        is_third = bool(ann) and gid == ann["id"]  # 按组 id,改名不可绕过(P3-4)
        if (is_third or "注释" in name) and not refine:
            QMessageBox.warning(
                self, "refine 必填",
                "组3(产物注释)的 refine 不能为空——清空会引入大量非质谱噪音。")
            return
        self.conn.execute(
            "UPDATE term_groups SET name=?, enabled=?, terms=?, refine=?"
            " WHERE id=?",
            (name, int(self.g_enabled.isChecked()),
             json.dumps(_parse_terms(self.g_terms.toPlainText()),
                        ensure_ascii=False),
             json.dumps(refine, ensure_ascii=False), gid),
        )
        self.conn.commit()
        self._reload_groups()
        self.group_list.setCurrentRow(row)

    # ------------------------------------------------------------- 订阅

    def _build_journals_page(self) -> QWidget:
        self.j_table = QTableWidget(0, 5)
        self.j_table.setHorizontalHeaderLabels(
            ["刊名", "ISSN", "模式", "路由", "启用"])
        self.j_add_name = QLineEdit()
        self.j_add_name.setPlaceholderText("刊名(如 Nature)或 ISSN")
        resolve = QPushButton("解析")
        resolve.clicked.connect(self._resolve_journal)
        self.j_matches = QComboBox()
        add = QPushButton("添加并回填 90 天")
        add.clicked.connect(self._add_journal)
        self.j_status = QLabel("")
        cancel = QPushButton("取消回填")
        cancel.clicked.connect(self._cancel_backfill)
        top = QHBoxLayout()
        for w in (self.j_add_name, resolve, self.j_matches, add, cancel):
            top.addWidget(w)
        del_btn = QPushButton("删除选中刊")
        del_btn.clicked.connect(self._del_journal)
        mode_btn = QPushButton("切换选中刊模式")
        mode_btn.clicked.connect(self._toggle_mode)
        audit_btn = QPushButton("校验全部刊模式(实测 90 天量)")
        audit_btn.clicked.connect(self._audit_modes)
        export_btn = QPushButton("导出订阅")
        export_btn.clicked.connect(self._export_subs)
        import_btn = QPushButton("导入订阅")
        import_btn.clicked.connect(self._import_subs)
        bar2 = QHBoxLayout()
        for b in (del_btn, mode_btn, audit_btn, export_btn, import_btn):
            bar2.addWidget(b)
        page = QWidget()
        v = QVBoxLayout(page)
        v.addLayout(top)
        v.addWidget(self.j_status)
        v.addWidget(self.j_table)
        v.addLayout(bar2)
        self._reload_journals()
        return page

    def _reload_journals(self) -> None:
        rows = [dict(r) for r in self.conn.execute(
            "SELECT * FROM journal_subscriptions ORDER BY id")]
        self.j_table.setColumnCount(5)  # 重置审计临时列(P2-6)
        self.j_table.setHorizontalHeaderLabels(
            ["刊名", "ISSN", "模式", "路由", "启用"])
        self.j_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for col, text in enumerate([
                r["name"], r["issn"], r["mode"], r["route"] or "—",
                "✓" if r["enabled"] else "✗",
            ]):
                self.j_table.setItem(i, col, QTableWidgetItem(text))
        self.j_table.setRowCount(len(rows))

    def _resolve_journal(self) -> None:
        text = self.j_add_name.text().strip()
        if not text:
            return
        self.j_status.setText("解析中…")

        def job():
            conn = store.connect()  # P0-2:worker 内自建
            store.init_db(conn)
            try:
                settings = store.load_settings(conn)
                return crossref.resolve_journal(
                    text, mailto=settings.get("crossref_mailto") or None)
            finally:
                conn.close()

        def done(res):
            if isinstance(res, Exception):
                self.j_status.setText(f"解析失败:{res}")
                return
            self.j_matches.clear()
            for title, issn in res[:10]:
                self.j_matches.addItem(f"{title} [{issn}]", (title, issn))
            self.j_status.setText(
                f"{len(res)} 个匹配(精确名优先)" if res else "未找到该刊")

        self._run_bg("resolve", job, done)

    def _add_journal(self) -> None:
        idx = self.j_matches.currentIndex()
        if idx < 0:
            self._resolve_journal()
            idx = self.j_matches.currentIndex()
            if idx < 0:
                return
        title, issn = self.j_matches.itemData(idx)
        if self._worker and self._worker.isRunning():  # 防重入
            self.j_status.setText("上一轮添加/回填仍在进行,请稍候。")
            return
        self.j_status.setText(f"添加 {title}:探测 90 天发文量…")
        self._worker = NewJournalWorker(title, issn)
        self._worker.stage.connect(lambda msg: self.j_status.setText(msg))
        self._worker.progress.connect(
            lambda n: self.j_status.setText(f"回填中…已处理 {n} 条"))
        self._worker.done.connect(
            lambda n: (self._reload_journals(),
                       self.j_status.setText(
                           self.j_status.text() if "失败" in self.j_status.text()
                           else f"回填完成,新增 {n} 篇")))
        self._worker.start()

    def _cancel_backfill(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self.j_status.setText("正在取消回填…")

    def _del_journal(self) -> None:
        row = self.j_table.currentRow()
        if row < 0:
            return
        issn = self.j_table.item(row, 1).text()
        self.conn.execute(
            "DELETE FROM journal_subscriptions WHERE issn=?", (issn,))
        self.conn.commit()
        self._reload_journals()

    def _toggle_mode(self) -> None:
        row = self.j_table.currentRow()
        if row < 0:
            return
        issn = self.j_table.item(row, 1).text()
        cur = self.conn.execute(
            "SELECT mode FROM journal_subscriptions WHERE issn=?",
            (issn,)).fetchone()
        new = "filter" if cur["mode"] == "full" else "full"
        self.conn.execute(
            "UPDATE journal_subscriptions SET mode=? WHERE issn=?",
            (new, issn))
        self.conn.commit()
        self._reload_journals()

    def _test_group(self) -> None:
        """用当前编辑中的短语构造查询,发一次真实请求预览命中。"""
        row = self.group_list.currentRow()
        if row < 0:
            return
        from datetime import date, timedelta

        from .. import queries as q_mod

        terms = _parse_terms(self.g_terms.toPlainText())
        refine = _parse_terms(self.g_refine.toPlainText())
        g = q_mod.TermGroup(id=None, name=self.g_name.text().strip() or "预览",
                            enabled=True,
                            terms=[q_mod.Term(**t) for t in terms],
                            refine=[q_mod.Term(**t) for t in refine])
        if not g.terms:
            QMessageBox.information(self, "测试该组", "请先填写至少一条短语。")
            return
        to = date.today()
        fr = to - timedelta(days=7)
        query = q_mod.to_europepmc_query(g, fr, to)
        self.g_status.setText("测试请求中…")
        self._tg_worker = TestGroupWorker(query)

        def _show(ok, payload):
            if ok:
                head = (f"近 7 天命中,前 {len(payload)} 条:" if payload
                        else "近 7 天命中 0 条(短语过窄或窗口无新文)。")
                body = chr(10).join(f"· {t}" for t in payload)
                QMessageBox.information(
                    self, "测试该组", head + chr(10) + body)
            else:
                QMessageBox.warning(self, "测试该组", f"请求失败:{payload}")
            self.g_status.setText("")

        self._tg_worker.done.connect(_show)
        self._tg_worker.start()

    def _audit_modes(self) -> None:
        w = self._bg_workers.get("audit")
        if w is not None and w.isRunning():
            self.j_status.setText("校验仍在进行,请稍候。")
            return
        self.j_status.setText("校验中(约 32 次请求)…")
        audit_worker = AuditWorker()

        def _show(rows):
            self.j_status.setText("")
            if isinstance(rows, Exception):
                QMessageBox.warning(self, "校验失败", str(rows)[:300])
                return
            # 表格补三列展示
            self.j_table.setColumnCount(8)
            self.j_table.setHorizontalHeaderLabels(
                ["刊名", "ISSN", "模式", "路由", "启用", "90天量", "建议", "不一致"])
            for i, r in enumerate(rows):
                self.j_table.setItem(i, 5, QTableWidgetItem(str(r["count_90d"])))
                self.j_table.setItem(i, 6, QTableWidgetItem(r["suggested"] or "-"))
                self.j_table.setItem(
                    i, 7, QTableWidgetItem("YES" if r["mismatch"] else ""))
            mism = [r for r in rows if r["mismatch"]]
            msg = (f"校验完成:{len(mism)} 本刊模式与建议不一致" if mism else "校验完成:全部一致")
            QMessageBox.information(self, "模式校验", msg)

        audit_worker.done.connect(_show)
        audit_worker.finished.connect(audit_worker.deleteLater)
        self._bg_workers["audit"] = audit_worker
        audit_worker.start()

    def _export_subs(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        data = journals.export_subscriptions(self.conn)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出订阅", "subscriptions.json", "JSON (*.json)")
        if not path:
            return
        import json as _json

        with open(path, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=1)
        self.j_status.setText(f"已导出 {len(data['journals'])} 刊 → {path}")

    def _import_subs(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "导入订阅", "", "JSON (*.json)")
        if not path:
            return
        import json as _json

        try:
            data = _json.loads(open(path, encoding="utf-8").read())
        except ValueError:
            QMessageBox.warning(self, "导入失败", "JSON 解析失败。")
            return
        added = journals.import_subscriptions(self.conn, data)
        self._reload_journals()
        self.j_status.setText(f"导入完成,新增 {added} 刊(按 ISSN 去重)")

    # ------------------------------------------------------------- 源与 LLM

    def _build_settings_page(self) -> QWidget:
        s = store.load_settings(self.conn)
        self.c_europepmc = QCheckBox("Europe PMC 主题检索")
        self.c_journals = QCheckBox("期刊订阅")
        self.c_biorxiv = QCheckBox("bioRxiv")
        for cb, key in ((self.c_europepmc, "src_europepmc_enabled"),
                        (self.c_journals, "src_journals_enabled"),
                        (self.c_biorxiv, "src_biorxiv_enabled")):
            cb.setChecked(s.get(key, "1") == "1")
            cb.stateChanged.connect(
                lambda _st, k=key, c=cb: store.save_setting(
                    self.conn, k, "1" if c.isChecked() else "0"))
        self.mailto = QLineEdit(s.get("crossref_mailto", ""))
        self.mailto.editingFinished.connect(
            lambda: store.save_setting(self.conn, "crossref_mailto",
                                       self.mailto.text().strip()))
        self.backfill = QSpinBox()
        self.backfill.setRange(7, 365)
        self.backfill.setValue(int(s.get("backfill_days", "30")))
        self.backfill.valueChanged.connect(
            lambda v: store.save_setting(self.conn, "backfill_days", str(v)))

        # 供应商 → 自动填 base_url;key → 拉取可用模型列表
        from PySide6.QtWidgets import QComboBox

        self.llm_provider = QComboBox()
        self.llm_provider.blockSignals(True)
        self.llm_provider.addItems(list(llm.PROVIDERS))
        self.llm_provider.blockSignals(False)
        self.llm_provider.currentTextChanged.connect(self._provider_changed)
        self.llm_url = QLineEdit(s.get("llm_base_url", ""))
        self.llm_url.setPlaceholderText("选择供应商自动填写,或自定义")
        self.llm_key = QLineEdit()
        self.llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_key.setPlaceholderText(
            "留空=保持已存 key;输入即覆盖(DPAPI 加密存储)")
        self.llm_model = QComboBox()
        self.llm_model.setEditable(True)
        self.llm_model.setCurrentText(s.get("llm_model", ""))
        fetch_models = QPushButton("获取模型列表(需已填 key)")
        fetch_models.clicked.connect(self._fetch_models)
        test = QPushButton("测试连接")
        test.clicked.connect(self._test_llm)

        def _save_llm():
            store.save_setting(self.conn, "llm_base_url",
                               self.llm_url.text().strip())
            store.save_setting(self.conn, "llm_model",
                               self.llm_model.currentText().strip())
            if self.llm_key.text().strip():
                store.save_setting(
                    self.conn, "llm_api_key",
                    llm.dpapi_encrypt(self.llm_key.text().strip()))

        for w, sig in ((self.llm_url, "editingFinished"),
                       (self.llm_key, "editingFinished")):
            getattr(w, sig).connect(_save_llm)
        self.llm_model.currentTextChanged.connect(
            lambda _t: store.save_setting(
                self.conn, "llm_model", self.llm_model.currentText().strip()))

        form = QFormLayout()
        form.addRow("Europe PMC", self.c_europepmc)
        form.addRow("期刊订阅", self.c_journals)
        form.addRow("bioRxiv", self.c_biorxiv)
        form.addRow("Crossref mailto(polite pool)", self.mailto)
        form.addRow("首跑回填天数", self.backfill)
        form.addRow("供应商", self.llm_provider)
        form.addRow("LLM base_url", self.llm_url)
        form.addRow("LLM api_key", self.llm_key)
        form.addRow("LLM model", self.llm_model)
        form.addRow("", fetch_models)
        form.addRow("", test)
        self._sync_provider_from_settings()
        page = QWidget()
        page.setLayout(form)
        return page

    def _test_llm(self) -> None:
        cfg = self._llm_cfg()
        if not cfg:
            QMessageBox.warning(self, "未配置", "请先填写 base_url 与 model。")
            return
        btn = self.sender()

        def job():
            return llm._chat(cfg, [{"role": "user", "content": "ping"}])

        def done(res):
            if btn:
                btn.setEnabled(True)
            if isinstance(res, Exception):
                QMessageBox.warning(self, "连接失败", str(res)[:300])
            else:
                QMessageBox.information(self, "连接成功", f"模型回复:{res[:80]}")

        if btn:
            btn.setEnabled(False)
        self._run_bg("llm_test", job, done)

    def _sync_provider_from_settings(self) -> None:
        """按已存 base_url 回显供应商;查不到选「自定义」(P2-1)。"""
        saved = (store.load_settings(self.conn).get("llm_base_url") or "").strip()
        match = "自定义"
        if saved:
            for name, info in llm.PROVIDERS.items():
                if info.get("base_url") and info["base_url"] == saved:
                    match = name
                    break
        self.llm_provider.blockSignals(True)
        self.llm_provider.setCurrentText(match)
        self.llm_provider.blockSignals(False)

    def _provider_changed(self, name: str) -> None:
        info = llm.PROVIDERS.get(name) or {}
        url = info.get("base_url", "")
        if url:
            self.llm_url.setText(url)
            store.save_setting(self.conn, "llm_base_url", url)
        if info.get("needs_key") is False:
            self.llm_key.setPlaceholderText("本地服务无需 key")
        else:
            self.llm_key.setPlaceholderText(
                "留空=保持已存 key;输入即覆盖(DPAPI 加密存储)")

    def _fetch_models(self) -> None:
        # 拉模型列表的目的就是选 model,不要求 model 已填(require_model=False)
        cfg = self._llm_cfg(require_model=False)
        if not cfg or not cfg.get("llm_base_url"):
            QMessageBox.warning(self, "未配置", "请先选择供应商或填写 base_url。")
            return
        self.j_status.setText("获取模型列表中…")

        def job():
            return llm.list_models(cfg)

        def done(res):
            self.j_status.setText("")
            if isinstance(res, Exception):
                QMessageBox.warning(
                    self, "获取失败",
                    f"{res}\n请检查 base_url 与 key。")
                return
            if not res:
                QMessageBox.information(self, "模型列表", "供应商返回空列表。")
                return
            cur = self.llm_model.currentText()
            self.llm_model.blockSignals(True)  # 刷新期间不写库(P2-2)
            self.llm_model.clear()
            self.llm_model.addItems(res)
            if cur in res:
                self.llm_model.setCurrentText(cur)
            else:
                self.llm_model.setCurrentText(cur)  # 保留原值
                self.j_status.setText(
                    f"当前模型 {cur} 不在返回列表中,已保留;可从下拉改选。")
            self.llm_model.blockSignals(False)

        self._run_bg("models", job, done)

    def _llm_cfg(self, require_model: bool = True) -> dict | None:
        s = store.load_settings(self.conn)
        if not s.get("llm_base_url"):
            return None
        if require_model and not s.get("llm_model"):
            return None
        key = ""
        if s.get("llm_api_key"):
            try:
                key = llm.dpapi_decrypt(s["llm_api_key"])
            except OSError:
                key = ""
        return dict(llm_base_url=s["llm_base_url"],
                    llm_model=s["llm_model"], llm_api_key=key)
