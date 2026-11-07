import ast
p = "paperpulse/gui/queries_view.py"
s = open(p, encoding="utf-8").read()

old = '''        self.llm_url = QLineEdit(s.get("llm_base_url", ""))
        self.llm_url.setPlaceholderText("如 https://open.bigmodel.cn/api/paas/v4")
        self.llm_key = QLineEdit()
        self.llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_key.setPlaceholderText(
            "留空=保持已存 key;输入即覆盖(DPAPI 加密存储)")
        self.llm_model = QLineEdit(s.get("llm_model", ""))
        test = QPushButton("测试连接")
        test.clicked.connect(self._test_llm)

        def _save_llm():
            store.save_setting(self.conn, "llm_base_url",
                               self.llm_url.text().strip())
            store.save_setting(self.conn, "llm_model",
                               self.llm_model.text().strip())
            if self.llm_key.text().strip():
                store.save_setting(
                    self.conn, "llm_api_key",
                    llm.dpapi_encrypt(self.llm_key.text().strip()))

        for w, sig in ((self.llm_url, "editingFinished"),
                       (self.llm_model, "editingFinished"),
                       (self.llm_key, "editingFinished")):
            getattr(w, sig).connect(_save_llm)'''
new = '''        # 供应商 → 自动填 base_url;key → 拉取可用模型列表
        from PySide6.QtWidgets import QComboBox

        self.llm_provider = QComboBox()
        self.llm_provider.addItems(list(llm.PROVIDERS))
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
                self.conn, "llm_model", self.llm_model.currentText().strip()))'''
assert old in s, "llm block"
s = s.replace(old, new)

old = '''        form.addRow("LLM base_url", self.llm_url)
        form.addRow("LLM api_key", self.llm_key)
        form.addRow("LLM model", self.llm_model)
        form.addRow("", test)'''
new = '''        form.addRow("供应商", self.llm_provider)
        form.addRow("LLM base_url", self.llm_url)
        form.addRow("LLM api_key", self.llm_key)
        form.addRow("LLM model", self.llm_model)
        form.addRow("", fetch_models)
        form.addRow("", test)'''
assert old in s, "form rows"
s = s.replace(old, new)

# handlers 追加到 _llm_cfg 前
anchor = "    def _llm_cfg(self) -> dict | None:"
assert anchor in s
s = s.replace(anchor, '''    def _provider_changed(self, name: str) -> None:
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
        cfg = self._llm_cfg()
        if not cfg or not cfg.get("llm_base_url"):
            QMessageBox.warning(self, "未配置", "请先选择供应商或填写 base_url。")
            return
        self.g_status.setText("获取模型列表中…")

        def job():
            return llm.list_models(cfg)

        def done(res):
            self.g_status.setText("")
            if isinstance(res, Exception):
                QMessageBox.warning(
                    self, "获取失败",
                    f"{res}\n请检查 base_url 与 key。")
                return
            if not res:
                QMessageBox.information(self, "模型列表", "供应商返回空列表。")
                return
            cur = self.llm_model.currentText()
            self.llm_model.clear()
            self.llm_model.addItems(res)
            if cur in res:
                self.llm_model.setCurrentText(cur)

        self._models_worker = FnWorker(job)
        self._models_worker.done.connect(done)
        self._models_worker.start()

    def _llm_cfg(self) -> dict | None:''', 1)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("gui ok")
