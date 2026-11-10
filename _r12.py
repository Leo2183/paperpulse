import ast

def load(p): return open(p, encoding="utf-8").read()
def save(p, s):
    open(p, "w", encoding="utf-8").write(s); ast.parse(s)
def rep(s, old, new, tag):
    assert old in s, f"MISS {tag}"
    return s.replace(old, new)

# ---- P0: FnWorker 去 deleteLater(守卫已足够;残留已结束对象最多 4 个)
p = "paperpulse/gui/queries_view.py"
s = load(p)
s = rep(s, '''    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.finished.connect(self.deleteLater)''',
'''    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        # 不接 deleteLater:自毁会让 _bg_workers 里的包装器失效,
        # 第二次点击在守卫处抛 RuntimeError(P0);已结束对象最多每 key 一个''', "P0")

# ---- P1a: _llm_cfg 补 llm_prompt(批量/测试连接两条链路一并修好)
s = rep(s, '''    def _llm_cfg(self, require_model: bool = True) -> dict | None:
        s = store.load_settings(self.conn)
        if not s.get("llm_base_url"):
            return None
        if require_model and not s.get("llm_model"):
            return None''',
'''    def _llm_cfg(self, require_model: bool = True) -> dict | None:
        s = store.load_settings(self.conn)
        if not s.get("llm_base_url"):
            return None
        if require_model and not s.get("llm_model"):
            return None
        # 自定义提示词随 cfg 下发(P1):批量导读/测试连接共用此构造''', "P1a head")
s = rep(s, '''        return dict(llm_base_url=s["llm_base_url"],
                    llm_model=s["llm_model"], llm_api_key=key)''',
'''        return dict(llm_base_url=s["llm_base_url"],
                    llm_model=s["llm_model"], llm_api_key=key,
                    llm_prompt=s.get("llm_prompt", ""))''', "P1a cfg")

# ---- P2-1: 设置页自己的状态标签(llm_status),模型列表反馈不再写别页
s = rep(s, '''        self.llm_prompt.setFixedHeight(72)''',
'''        self.llm_prompt.setFixedHeight(72)
        self.llm_status = QLabel("")''', "llm_status")
s = rep(s, '''        form.addRow("总结重点(自定义提示词)", self.llm_prompt)''',
'''        form.addRow("总结重点(自定义提示词)", self.llm_prompt)
        form.addRow("", self.llm_status)''', "llm_status row")
s = rep(s, '''        self.j_status.setText("获取模型列表中…")''',
'''        self.llm_status.setText("获取模型列表中…")''', "fetch busy")
s = rep(s, '''        def done(res):
            self.j_status.setText("")
            if isinstance(res, Exception):
                QMessageBox.warning(
                    self, "获取失败",''',
'''        def done(res):
            self.llm_status.setText("")
            if isinstance(res, Exception):
                QMessageBox.warning(
                    self, "获取失败",''', "fetch done")
s = rep(s, '''                self.j_status.setText(
                    f"当前模型 {cur} 不在返回列表中,已保留;可从下拉改选。")''',
'''                self.llm_status.setText(
                    f"当前模型 {cur} 不在返回列表中,已保留;可从下拉改选。")''', "fetch keep")

# ---- P2-2: 导出默认路径进 data/(防误落仓库根)
s = rep(s, '''        path, _ = QFileDialog.getSaveFileName(
            self, "导出订阅", "subscriptions.json", "JSON (*.json)")''',
'''        default = config.data_dir() / "subscriptions.json"  # 不落 cwd(防误入库)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出订阅", str(default), "JSON (*.json)")''', "export path")
if "from .. import config, store" not in s and "from .. import config" not in s:
    s = rep(s, "from .. import store", "from .. import config, store", "cfg import")
save(p, s)
print("queries_view ok")

# ---- P1b: _gen_digest 复用带提示词的 cfg + 已有缓存时确认覆盖
p = "paperpulse/gui/list_view.py"
s = load(p)
old = '''        from .. import llm

        settings = store.load_settings(self.conn)
        if not (settings.get("llm_base_url") and settings.get("llm_model")):
            QMessageBox.warning(
                self, "LLM 未配置",
                "请先在「检索式配置 → 源与 LLM」填写 LLM base_url / model / key。")
            return
        cfg = dict(
            llm_base_url=settings["llm_base_url"],
            llm_model=settings["llm_model"],
            llm_api_key=llm.dpapi_decrypt(settings["llm_api_key"])
            if settings.get("llm_api_key") else "",
        )
        ok, fail = llm.batch_digest(self.conn, [dict(r)], cfg)'''
new = '''        from .. import llm

        settings = store.load_settings(self.conn)
        if not (settings.get("llm_base_url") and settings.get("llm_model")):
            QMessageBox.warning(
                self, "LLM 未配置",
                "请先在「检索式配置 → 源与 LLM」填写 LLM base_url / model / key。")
            return
        if r.get("llm_digest"):
            ans = QMessageBox.question(
                self, "覆盖确认",
                "该篇已有导读,用当前提示词重新生成并覆盖?")
            if ans != QMessageBox.StandardButton.Yes:
                return
            self.conn.execute(
                "UPDATE papers SET llm_digest=NULL WHERE id=?", (r["id"],))
            self.conn.commit()
            r = dict(r, llm_digest=None)
        cfg = dict(
            llm_base_url=settings["llm_base_url"],
            llm_model=settings["llm_model"],
            llm_api_key=llm.dpapi_decrypt(settings["llm_api_key"])
            if settings.get("llm_api_key") else "",
            llm_prompt=settings.get("llm_prompt", ""),  # P1:自定义提示词
        )
        ok, fail = llm.batch_digest(self.conn, [dict(r)], cfg)'''
assert old in s, "MISS _gen_digest"
s = s.replace(old, new)
save(p, s)
print("list_view ok")

# ---- P2-4: 订阅导入形状守卫
p = "paperpulse/sources/journals.py"
s = load(p)
s = rep(s, '''def import_subscriptions(conn, data: dict) -> int:
    """合并导入订阅(按 ISSN 去重);返回新增刊数。"""
    existing = {r["issn"] for r in conn.execute(
        "SELECT issn FROM journal_subscriptions")}''',
'''def import_subscriptions(conn, data: dict) -> int:
    """合并导入订阅(按 ISSN 去重);返回新增刊数。"""
    if not isinstance(data, dict):
        raise ValueError(f"订阅文件顶层必须是对象,得到 {type(data).__name__}")
    rows_in = data.get("journals", [])
    if not isinstance(rows_in, list):
        raise ValueError('"journals" 必须是数组')
    existing = {r["issn"] for r in conn.execute(
        "SELECT issn FROM journal_subscriptions")}''', "P2-4")
save(p, s)
print("journals ok")

# ---- P2-2: subscriptions.json 出库 + ignore
import subprocess
subprocess.run(["git", "rm", "--cached", "subscriptions.json"], check=False)
gi = open(".gitignore", encoding="utf-8").read()
if "subscriptions.json" not in gi:
    gi = gi.rstrip("\n") + "\nsubscriptions.json\n"
    open(".gitignore", "w", encoding="utf-8").write(gi)
print("gitignore ok")
